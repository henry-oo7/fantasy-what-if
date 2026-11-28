"""Orphaned canonical League retention and purge (ADR 0010, FR-18).

A League is orphaned (``orphaned_at`` set) when its last *active* access
disappears and no valid temporary workspace claim backs it. It is retained for
``ORPHAN_RETENTION``. A successful import in that window clears the mark
under the League lock and the same rows continue.

Purge is a durable ``orphan_league_purge`` job per League with the exact payload
``{"version": 1, "league_id": N}`` and key ``orphan-league-purge:{N}``. The
scheduler's scan (:func:`enqueue_due_orphan_purges`) only decides which due
Leagues to enqueue; the worker (:func:`purge_orphaned_league`) is the only
deleter and re-decides everything under the League row lock:

1. the League still exists and is still orphaned;
2. the retention deadline has elapsed;
3. zero ``active`` access rows exist on any of its Seasons;
4. no valid workspace claim backs it.

Any other outcome is a successful no-op, so a delayed or duplicate job is
always safe: lateness only keeps data longer, never deletes it early. Import
and access creation take the same League lock before they insert, so a
committed access row is always visible here.

Load bound: one indexed discovery query per scan, at most
``PURGE_BATCH_LIMIT`` jobs per scan.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import String, cast, delete, exists, false, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobType,
    UserSeasonAccessStatus,
)
from app.db.platform_models import BackgroundJob
from app.repositories.background_job_repository import (
    ACTIVE_JOB_STATUSES,
    BackgroundJobRepository,
)
from app.repositories.provider_league_claim_repository import (
    claimed_workspace_id,
    workspace_league_condition,
)
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    BackgroundJobSnapshot,
)
from app.services.provider_sync import SyncJobAuthority, require_job_authority
from app.services.synchronization_job_contract import (
    ORPHAN_LEAGUE_PURGE_IDEMPOTENCY_PREFIX,
    ORPHAN_LEAGUE_PURGE_PAYLOAD_VERSION,
    InvalidOrphanLeaguePurgePayloadError,
    build_orphan_league_purge_payload,
    orphan_league_purge_idempotency_key,
    validate_orphan_league_purge_payload,
)

LOGGER = logging.getLogger(__name__)

ORPHAN_RETENTION = timedelta(days=30)
PURGE_BATCH_LIMIT = 100
PURGE_PRIORITY = 30
PURGE_MAXIMUM_ATTEMPTS = 5


class PurgeOutcome(StrEnum):
    PURGED = "purged"
    NOT_FOUND = "not_found"
    NOT_ORPHANED = "not_orphaned"
    NOT_DUE = "not_due"
    ACTIVE_ACCESS = "active_access"
    WORKSPACE_DEPENDENCY = "workspace_dependency"


def list_due_orphan_league_ids(
    session: Session, *, now: datetime, limit: int = PURGE_BATCH_LIMIT
) -> list[int]:
    """Due Leagues without active access, workspace dependency or an active purge job."""

    active_access = exists(
        select(UserLeagueSeasonAccess.id)
        .join(Season, Season.id == UserLeagueSeasonAccess.season_id)
        .where(
            Season.league_id == League.id,
            UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
        )
    )
    active_purge_job = exists(
        select(BackgroundJob.id).where(
            BackgroundJob.job_type == BackgroundJobType.ORPHAN_LEAGUE_PURGE,
            BackgroundJob.idempotency_key
            == ORPHAN_LEAGUE_PURGE_IDEMPOTENCY_PREFIX + cast(League.id, String),
            BackgroundJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )

    return list(
        session.scalars(
            select(League.id)
            .where(
                League.orphaned_at.is_not(None),
                League.orphaned_at <= now - ORPHAN_RETENTION,
                ~active_access,
                ~func.coalesce(workspace_league_condition(League.installation_id), false()),
                ~active_purge_job,
            )
            .order_by(League.orphaned_at, League.id)
            .limit(limit)
        )
    )


def enqueue_due_orphan_purges(
    session: Session, *, now: datetime, limit: int = PURGE_BATCH_LIMIT
) -> int:
    """Enqueue one purge job per due League in the caller's transaction; return the new count."""

    jobs = BackgroundJobRepository(session)
    created = 0

    for league_id in list_due_orphan_league_ids(session, now=now, limit=limit):
        result = jobs.enqueue(
            workspace_id=None,
            job_type=BackgroundJobType.ORPHAN_LEAGUE_PURGE,
            payload=build_orphan_league_purge_payload(league_id),
            idempotency_key=orphan_league_purge_idempotency_key(league_id),
            payload_version=ORPHAN_LEAGUE_PURGE_PAYLOAD_VERSION,
            priority=PURGE_PRIORITY,
            maximum_attempts=PURGE_MAXIMUM_ATTEMPTS,
            available_at=now,
        )
        created += not result.existing

    return created


def purge_orphaned_league(session: Session, *, league_id: int, now: datetime) -> PurgeOutcome:
    """Delete one League graph only if it is still orphaned, due, unused and unclaimed.

    Runs in the caller's transaction; the caller commits. The League row lock
    is the authority: import, access creation and orphan marking take it too.
    """

    league = session.scalar(
        select(League)
        .where(League.id == league_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if league is None:
        return PurgeOutcome.NOT_FOUND

    if league.orphaned_at is None:
        return PurgeOutcome.NOT_ORPHANED

    if league.orphaned_at > now - ORPHAN_RETENTION:
        return PurgeOutcome.NOT_DUE

    if claimed_workspace_id(session, league) is not None:
        return PurgeOutcome.WORKSPACE_DEPENDENCY

    if session.scalar(
        select(
            exists().where(
                UserLeagueSeasonAccess.season_id == Season.id,
                Season.league_id == league_id,
                UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
            )
        )
    ):
        return PurgeOutcome.ACTIVE_ACCESS

    # The database cascades the Season graph; nothing else references a League.
    session.execute(delete(League).where(League.id == league_id))

    return PurgeOutcome.PURGED


class OrphanLeaguePurgeJobHandler:
    """Execute one ``orphan_league_purge`` job in a single transaction."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.now = now

    def __call__(self, job: BackgroundJobSnapshot) -> None:
        try:
            league_id = validate_orphan_league_purge_payload(
                job.payload, payload_version=job.payload_version
            )
        except InvalidOrphanLeaguePurgePayloadError as exc:
            raise BackgroundJobExecutionError(
                safe_error_code="invalid_job_payload",
                safe_error_message="The background job payload is invalid.",
            ) from exc

        with self.session_factory() as session:
            try:
                # A worker whose lease was recovered must not delete anything.
                require_job_authority(
                    session, SyncJobAuthority(job_id=job.id, attempt_count=job.attempt_count)
                )
                outcome = purge_orphaned_league(session, league_id=league_id, now=self.now())
                session.commit()
            except OperationalError:
                session.rollback()
                raise BackgroundJobExecutionError(
                    safe_error_code="orphan_purge_database_retryable",
                    safe_error_message="The League purge could not complete. It will retry.",
                    retryable=True,
                ) from None
            except IntegrityError:
                session.rollback()
                raise BackgroundJobExecutionError(
                    safe_error_code="orphan_purge_integrity",
                    safe_error_message="The League purge could not complete.",
                ) from None

        LOGGER.info("Orphan League purge finished: job_id=%s, outcome=%s.", job.id, outcome.value)
