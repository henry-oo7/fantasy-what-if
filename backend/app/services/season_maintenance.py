"""Recurring personal League Season maintenance (ADR 0010).

Discovery is independent of workspaces: it reads only Seasons and per-user
access rows. Two kinds of durable job result, and the scheduler's scan only
decides *which* to enqueue, never performs provider work:

- the existing Season-owned synchronization job (``league-season-sync:{id}``),
  shared with every other producer, so a Season never has two active jobs;
- one reverification job per user and Season
  (``access-reverification:{user}:{season}``).

Policy (initial values, no new settings or columns):

- A Season refreshes when its last *successful* synchronization is older than
  ``SEASON_REFRESH_INTERVAL`` (the weekly cadence of the workspace schedule),
  and not more often than ``SEASON_RETRY_INTERVAL`` (daily) after any attempt, so a
  failing Season is retried at most daily.
- Only Seasons of the current football year (until four months after it ends)
  refresh: older imported Seasons are complete and ESPN is not re-polled.
- A Season is a candidate only while a credential source exists: an active
  public-proof user, or an active private-proof user whose own connection is
  usable. Orphaned or access-less Leagues are never scheduled, and a Season
  that has no usable source is not queued just to fail.
- An access row is due seven days after its last successful proof
  (``last_reverified_at``, else ``verified_at``); a ``reverification_required``
  row is retried seven days after it was last flagged (its ``updated_at``).
  ``revoked`` rows are never reverified.

Load bound: at most ``MAINTENANCE_BATCH_LIMIT`` of each kind per scan, jobs
staggered ``JOB_STAGGER`` apart, two discovery queries per scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db.auth_models import User
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import ProviderConnection
from app.services.access_reverification import (
    REVERIFICATION_INTERVAL,
    enqueue_access_reverification,
)
from app.services.season_sync_jobs import enqueue_season_sync

SEASON_REFRESH_INTERVAL = timedelta(days=7)
SEASON_RETRY_INTERVAL = timedelta(hours=24)
MAINTENANCE_SCAN_INTERVAL = timedelta(hours=6)
MAINTENANCE_BATCH_LIMIT = 100
JOB_STAGGER = timedelta(seconds=10)
SEASON_YEAR_GRACE = timedelta(days=122)
MAINTENANCE_SYNC_PRIORITY = 15


@dataclass(frozen=True, slots=True)
class MaintenanceBatch:
    """Safe counts only: no user, Season, connection or job identifiers."""

    sync_created: int
    sync_existing: int
    reverification_created: int
    reverification_existing: int


def list_due_season_syncs(
    session: Session, *, now: datetime, limit: int = MAINTENANCE_BATCH_LIMIT
) -> list[int]:
    """Season IDs due for refresh, oldest first, ascending within the batch."""

    active_user = and_(
        UserLeagueSeasonAccess.season_id == Season.id,
        UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
        User.is_active.is_(True),
    )
    public_source = exists(
        select(UserLeagueSeasonAccess.id)
        .join(User, User.id == UserLeagueSeasonAccess.user_id)
        .where(active_user, UserLeagueSeasonAccess.proof_mode == UserSeasonProofMode.PUBLIC)
    )
    private_source = exists(
        select(UserLeagueSeasonAccess.id)
        .join(User, User.id == UserLeagueSeasonAccess.user_id)
        .join(
            ProviderConnection,
            ProviderConnection.id == UserLeagueSeasonAccess.provider_connection_id,
        )
        .where(
            active_user,
            UserLeagueSeasonAccess.proof_mode == UserSeasonProofMode.PRIVATE_CREDENTIALS,
            ProviderConnection.user_id == UserLeagueSeasonAccess.user_id,
            ProviderConnection.provider == League.provider,
            ProviderConnection.is_active.is_(True),
            ProviderConnection.encrypted_credentials.is_not(None),
            ProviderConnection.credential_version.is_not(None),
            ProviderConnection.last_validation_error.is_(None),
        )
    )

    ids = session.scalars(
        select(Season.id)
        .join(League, League.id == Season.league_id)
        .where(
            League.provider == FantasyProvider.ESPN,
            League.provider_league_id.is_not(None),
            League.orphaned_at.is_(None),
            Season.year >= (now - SEASON_YEAR_GRACE).year,
            or_(
                Season.sync_state.is_(None),
                Season.sync_state.not_in((SeasonSyncState.QUEUED, SeasonSyncState.SYNCING)),
            ),
            or_(
                Season.last_successful_sync_at.is_(None),
                Season.last_successful_sync_at <= now - SEASON_REFRESH_INTERVAL,
            ),
            or_(
                Season.last_sync_attempt_at.is_(None),
                Season.last_sync_attempt_at <= now - SEASON_RETRY_INTERVAL,
            ),
            or_(public_source, private_source),
        )
        .order_by(Season.last_successful_sync_at.asc().nulls_first(), Season.id)
        .limit(limit)
    ).all()

    return sorted(ids)


def list_due_reverifications(
    session: Session, *, now: datetime, limit: int = MAINTENANCE_BATCH_LIMIT
) -> list[tuple[int, int]]:
    """``(user_id, season_id)`` pairs whose own proof is due, oldest proof first."""

    cutoff = now - REVERIFICATION_INTERVAL
    last_proof = func.coalesce(
        UserLeagueSeasonAccess.last_reverified_at, UserLeagueSeasonAccess.verified_at
    )
    rows = session.execute(
        select(UserLeagueSeasonAccess.user_id, UserLeagueSeasonAccess.season_id)
        .join(User, User.id == UserLeagueSeasonAccess.user_id)
        .join(Season, Season.id == UserLeagueSeasonAccess.season_id)
        .join(League, League.id == Season.league_id)
        .where(
            User.is_active.is_(True),
            League.provider == FantasyProvider.ESPN,
            League.orphaned_at.is_(None),
            or_(
                and_(
                    UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
                    last_proof <= cutoff,
                ),
                and_(
                    UserLeagueSeasonAccess.status == UserSeasonAccessStatus.REVERIFICATION_REQUIRED,
                    UserLeagueSeasonAccess.updated_at <= cutoff,
                ),
            ),
        )
        .order_by(last_proof, UserLeagueSeasonAccess.id)
        .limit(limit)
    ).all()

    return [(user_id, season_id) for user_id, season_id in rows]


def enqueue_maintenance(session: Session, *, now: datetime) -> MaintenanceBatch:
    """Enqueue every due refresh and reverification in the caller's transaction."""

    sync_results = [
        enqueue_season_sync(
            session,
            season_id=season_id,
            priority=MAINTENANCE_SYNC_PRIORITY,
            available_at=now + index * JOB_STAGGER,
        )
        for index, season_id in enumerate(list_due_season_syncs(session, now=now))
    ]
    reverification_results = [
        enqueue_access_reverification(
            session,
            user_id=user_id,
            season_id=season_id,
            available_at=now + index * JOB_STAGGER,
        )
        for index, (user_id, season_id) in enumerate(list_due_reverifications(session, now=now))
    ]

    return MaintenanceBatch(
        sync_created=sum(not result.existing for result in sync_results),
        sync_existing=sum(result.existing for result in sync_results),
        reverification_created=sum(not result.existing for result in reverification_results),
        reverification_existing=sum(result.existing for result in reverification_results),
    )
