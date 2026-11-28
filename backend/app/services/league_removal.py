"""Personal "Remove League from my account" (ADR 0010, FR-16).

Removes only the requesting user's access to every Season of one canonical
League. It never deletes the user, the League or its Seasons, another user's
access, preference, session, connection or job, or a shared synchronization
job. One transaction, caller commits. Lock order, matching import and account
deletion: the user row, then the League row.

Credentials follow access: the user's connections for the League that no
remaining access row references are deleted; a connection another access row
of the same user still uses is kept. A League left with no active access and no
valid workspace claim is orphaned (``reverification_required`` never counts).
Every refusal that depends on the League or the user's access is one identical
``LeagueRemovalUnavailableError``, so a missing League and one the user cannot
access look the same.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, exists, select, update
from sqlalchemy.orm import Session

from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    UserSeasonAccessStatus,
)
from app.db.platform_models import BackgroundJob, ProviderConnection
from app.repositories.background_job_repository import QUEUED_JOB_STATUSES
from app.repositories.league_access_repository import LeagueAccessRepository
from app.repositories.provider_league_claim_repository import normalize_provider_league_id
from app.services.account_deletion import AccountDeletionError, lock_personal_user

LEAGUE_REMOVAL_REQUEST_ACTION = "league_removal_request"
LEAGUE_REMOVAL_MAXIMUM_ATTEMPTS = 20
LEAGUE_REMOVAL_ATTEMPT_WINDOW_MINUTES = 60
_READABLE = (UserSeasonAccessStatus.ACTIVE, UserSeasonAccessStatus.REVERIFICATION_REQUIRED)


class LeagueRemovalError(RuntimeError):
    code = "league_removal_unavailable"


class LeagueRemovalUnavailableError(LeagueRemovalError):
    """Ineligible caller, unknown League, or no readable access: one outcome."""


class LeagueRemovalConfirmationError(LeagueRemovalError):
    code = "league_removal_confirmation_mismatch"


def normalized_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def remove_league_access(
    session: Session,
    *,
    user_id: int,
    auth_session_id: int,
    provider: FantasyProvider,
    provider_league_id: str,
    confirmation: str,
    now: datetime | None = None,
) -> None:
    """Remove this user's access to the League; the caller commits."""

    current_time = now or datetime.now(UTC)

    try:
        lock_personal_user(
            session, user_id=user_id, auth_session_id=auth_session_id, now=current_time
        )
    except AccountDeletionError:
        raise LeagueRemovalUnavailableError("League removal is unavailable.") from None

    repository = LeagueAccessRepository(session)

    try:
        league = repository.get_league_by_identity(
            provider=provider, provider_league_id=normalize_provider_league_id(provider_league_id)
        )
    except ValueError:
        league = None

    if league is None:
        raise LeagueRemovalUnavailableError("League removal is unavailable.")

    # User before League, as in import and account deletion.
    league = session.scalar(
        select(League)
        .where(League.id == league.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if league is None:
        raise LeagueRemovalUnavailableError("League removal is unavailable.")

    season_ids = select(Season.id).where(Season.league_id == league.id)
    mine = (
        UserLeagueSeasonAccess.user_id == user_id,
        UserLeagueSeasonAccess.season_id.in_(season_ids),
    )

    if not session.scalar(
        select(exists().where(*mine, UserLeagueSeasonAccess.status.in_(_READABLE)))
    ):
        raise LeagueRemovalUnavailableError("League removal is unavailable.")

    if normalized_name(confirmation) != normalized_name(league.name):
        raise LeagueRemovalConfirmationError("The confirmation does not match.")

    session.execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.job_type == BackgroundJobType.ACCESS_REVERIFICATION,
            BackgroundJob.status.in_(QUEUED_JOB_STATUSES),
            BackgroundJob.payload["user_id"].as_integer() == user_id,
            BackgroundJob.payload["season_id"].as_integer().in_(season_ids),
        )
        .values(
            status=BackgroundJobStatus.CANCELED,
            canceled_at=current_time,
            completed_at=None,
            failed_at=None,
            claimed_at=None,
            lease_expires_at=None,
            claimed_by=None,
            started_at=None,
            retryable=None,
        )
        .execution_options(synchronize_session=False)
    )

    used_connections = select(UserLeagueSeasonAccess.provider_connection_id).where(*mine)
    candidate_ids = list(
        session.scalars(
            select(ProviderConnection.id).where(
                ProviderConnection.user_id == user_id,
                (ProviderConnection.id.in_(used_connections))
                | (
                    (ProviderConnection.provider == league.provider)
                    & (ProviderConnection.provider_league_id == league.provider_league_id)
                ),
            )
        )
    )

    session.execute(
        delete(UserLeagueSeasonAccess).where(*mine).execution_options(synchronize_session=False)
    )
    session.execute(
        update(UserPreference)
        .where(
            UserPreference.user_id == user_id,
            UserPreference.default_league_season_id.in_(season_ids),
        )
        .values(default_league_season_id=None)
        .execution_options(synchronize_session=False)
    )

    unreferenced = [
        connection_id
        for connection_id in candidate_ids
        if not session.scalar(
            select(exists().where(UserLeagueSeasonAccess.provider_connection_id == connection_id))
        )
    ]

    if unreferenced:
        # A Season never keeps a pointer to a credential that is about to be deleted.
        session.execute(
            update(Season)
            .where(Season.provider_connection_id.in_(unreferenced))
            .values(provider_connection_id=None)
            .execution_options(synchronize_session=False)
        )
        session.execute(
            delete(ProviderConnection)
            .where(ProviderConnection.id.in_(unreferenced), ProviderConnection.user_id == user_id)
            .execution_options(synchronize_session=False)
        )

    repository.mark_league_orphaned(league_id=league.id, orphaned_at=current_time)
    session.flush()
