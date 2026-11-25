"""The single enqueue path for League-Season synchronization (ADR 0010).

Every producer (manual refresh, scheduler, compatibility adapters, and the
future League import) calls :func:`enqueue_season_sync`. The active
idempotency key ``league-season-sync:{season_id}`` and the partial unique
index allow at most one active job per Season across every workspace and
user; repeated calls return the active job.

The job's ``workspace_id`` is derived from the Season's own League, never
from the caller: transitional, so a workspace Season's job stays visible to
that workspace's job-status reads and is canceled by its deletion. It is
NULL for a League without a workspace, and it is never in the payload or key.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import League, Season, SyncRun, SyncStatus
from app.db.platform_enums import BackgroundJobType, SeasonSyncState
from app.db.platform_models import BackgroundJob, Installation
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
    EnqueueBackgroundJobResult,
)
from app.repositories.provider_league_claim_repository import workspace_league_condition
from app.services.season_sync_state import mark_failed, mark_queued, mark_requeued
from app.services.synchronization_job_contract import (
    SEASON_SYNC_PAYLOAD_VERSION,
    InvalidSeasonSyncPayloadError,
    build_season_sync_payload,
    season_sync_idempotency_key,
    validate_season_sync_payload,
)

SEASON_SYNC_DEFAULT_PRIORITY = 25
SEASON_SYNC_MAXIMUM_ATTEMPTS = 5
EXPIRED_LEASE_SYNC_CODE = "worker_lease_expired"
LEASE_EXPIRED_SYNC_MESSAGE = "The background worker stopped before the synchronization completed."


class SeasonSyncSeasonNotFoundError(LookupError):
    """The Season to synchronize does not exist."""


def enqueue_season_sync(
    session: Session,
    *,
    season_id: int,
    priority: int = SEASON_SYNC_DEFAULT_PRIORITY,
    available_at: datetime | None = None,
) -> EnqueueBackgroundJobResult:
    """Create or return the Season's one active synchronization job.

    Runs in the caller's transaction; the caller commits. ``priority`` only
    applies when a new job is created.
    """

    # The column is passed as the workspace: NULL, or a workspace without a
    # backing claim, yields no row, so the job carries no workspace.
    workspace_id = session.scalar(
        select(League.installation_id)
        .join(Season, Season.league_id == League.id)
        .where(Season.id == season_id, workspace_league_condition(League.installation_id))
    )

    if workspace_id is not None:
        # Workspace before Season, matching persistence and deletion; the job
        # foreign key would otherwise take this lock after the Season lock.
        session.scalar(
            select(Installation.id)
            .where(Installation.id == workspace_id)
            .with_for_update(read=True, key_share=True)
        )

    # The Season row lock serializes enqueue with attempt preparation, so the
    # queued state can never overwrite an attempt that is already syncing.
    season = session.scalar(select(Season).where(Season.id == season_id).with_for_update())

    if season is None:
        raise SeasonSyncSeasonNotFoundError("The Season to synchronize does not exist.")

    result = BackgroundJobRepository(session).enqueue(
        workspace_id=workspace_id,
        job_type=BackgroundJobType.LEAGUE_SEASON_SYNC,
        payload=build_season_sync_payload(season_id),
        idempotency_key=season_sync_idempotency_key(season_id),
        payload_version=SEASON_SYNC_PAYLOAD_VERSION,
        priority=priority,
        maximum_attempts=SEASON_SYNC_MAXIMUM_ATTEMPTS,
        available_at=available_at,
    )

    if not result.existing:
        mark_queued(season)
        session.flush()

    return result


def apply_season_sync_lease_recovery(
    session: Session,
    *,
    job: BackgroundJob,
    retrying: bool,
) -> None:
    """Move a Season out of ``syncing`` when its worker's lease expired.

    Runs in the lease-recovery transaction that holds the job row lock. The
    abandoned attempt's SyncRun fails, so a resurrected worker cannot persist
    with it; the Season returns to ``queued`` while a retry is scheduled, or to
    ``stale``/``failed`` when attempts are exhausted. A Season locked by a live
    attempt is skipped: that transaction owns its state.
    """

    if job.job_type is not BackgroundJobType.LEAGUE_SEASON_SYNC:
        return

    try:
        season_id = validate_season_sync_payload(job.payload, payload_version=job.payload_version)
    except InvalidSeasonSyncPayloadError:
        return

    season = session.scalar(
        select(Season).where(Season.id == season_id).with_for_update(skip_locked=True)
    )

    if season is None or season.sync_state is not SeasonSyncState.SYNCING:
        return

    session.execute(
        update(SyncRun)
        .where(SyncRun.season_id == season_id, SyncRun.status == SyncStatus.RUNNING)
        .values(
            status=SyncStatus.FAILED,
            completed_at=datetime.now(UTC),
            safe_error_message=LEASE_EXPIRED_SYNC_MESSAGE,
            analytics_rebuilt=False,
        )
    )

    if retrying:
        mark_requeued(season)
    else:
        mark_failed(season, failure_code=EXPIRED_LEASE_SYNC_CODE)

    session.flush()
