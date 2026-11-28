"""Per-user League Season access reverification (ADR 0010).

One durable ``access_reverification`` job proves ONE user's access to ONE
Season again. Sequence (no provider network call runs inside a transaction):

Prepare  Short committed transaction: confirm the job still owns the attempt,
         read the user's access row, and load only this user's own stored
         credentials for the exact target.
Proof    No Session open. ESPN anonymous first; only this user's own
         credentials if ESPN requires authentication. Another user's
         connection, a workspace claim and the Season's synchronization
         credential are never read, so one user's proof is never another's.
Persist  Short transaction: recheck job authority and that the access row and
         connection are exactly what was planned; otherwise the result is
         discarded and nothing is written.

Outcomes (ESPN evidence is never definitive about lost membership, so no
outcome revokes; ``revoked`` stays reserved for explicit removal and future
provider evidence):

- anonymous access works        -> public proof, access active, connection kept
- own credentials work          -> private proof, access active, connection valid
- credentials rejected (401)    -> connection marked invalid, access ->
                                   reverification_required
- credentials required but none -> access -> reverification_required (and the
  usable                           Season's other public-proof users are
                                   queued to prove themselves independently)
- ambiguous 403/404/malformed   -> access -> reverification_required
- timeout, 5xx, 429             -> nothing changes; the job retries
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db.auth_models import User
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobType,
    FantasyProvider,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import ProviderConnection
from app.providers.espn.league_access import (
    EspnLeagueAccessOutcome,
    EspnPrivateAccess,
    HttpGet,
)
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
    EnqueueBackgroundJobResult,
)
from app.services.background_job_worker import BackgroundJobExecutionError
from app.services.provider_adapter_resolver import (
    ProviderAdapterResolutionError,
    resolve_connection_credentials,
)
from app.services.provider_onboarding import check_league_access, private_access_from_values
from app.services.provider_sync import SyncJobAuthority, credential_marker, require_job_authority
from app.services.synchronization_job_contract import (
    ACCESS_REVERIFICATION_PAYLOAD_VERSION,
    access_reverification_idempotency_key,
    build_access_reverification_payload,
)

LOGGER = logging.getLogger(__name__)

REVERIFICATION_INTERVAL = timedelta(days=7)
REVERIFICATION_PRIORITY = 20
REVERIFICATION_MAXIMUM_ATTEMPTS = 3
CREDENTIALS_REJECTED_CODE = "credentials_rejected"
_READABLE = (UserSeasonAccessStatus.ACTIVE, UserSeasonAccessStatus.REVERIFICATION_REQUIRED)


class ReverificationOutcome(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    CREDENTIALS_REJECTED = "credentials_rejected"
    RECONNECT_REQUIRED = "reconnect_required"
    DISCARDED = "discarded"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ReverificationPlan:
    """Detached preparation snapshot: identifiers and a credential digest only."""

    access_id: int
    user_id: int
    season_id: int
    provider_league_id: str
    season_year: int
    status: UserSeasonAccessStatus
    proof_mode: UserSeasonProofMode
    access_connection_id: int | None
    connection_id: int | None
    credential_marker: str | None
    job_authority: SyncJobAuthority | None


def enqueue_access_reverification(
    session: Session,
    *,
    user_id: int,
    season_id: int,
    priority: int = REVERIFICATION_PRIORITY,
    available_at: datetime | None = None,
) -> EnqueueBackgroundJobResult:
    """Create or return this user's one active reverification job for the Season.

    Runs in the caller's transaction; the caller commits. The job never
    carries a workspace.
    """

    return BackgroundJobRepository(session).enqueue(
        workspace_id=None,
        job_type=BackgroundJobType.ACCESS_REVERIFICATION,
        payload=build_access_reverification_payload(user_id, season_id),
        idempotency_key=access_reverification_idempotency_key(user_id, season_id),
        payload_version=ACCESS_REVERIFICATION_PAYLOAD_VERSION,
        priority=priority,
        maximum_attempts=REVERIFICATION_MAXIMUM_ATTEMPTS,
        available_at=available_at,
    )


def reverify_access(
    *,
    session_factory: sessionmaker[Session],
    user_id: int,
    season_id: int,
    settings: Settings,
    job_authority: SyncJobAuthority | None = None,
    http_get: HttpGet | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ReverificationOutcome:
    """Prepare, prove with no transaction open, then persist one user's reverification."""

    prepared = _prepare(
        session_factory,
        user_id=user_id,
        season_id=season_id,
        settings=settings,
        job_authority=job_authority,
    )

    if prepared is None:
        return ReverificationOutcome.NOT_APPLICABLE

    plan, private_access = prepared
    result, access_mode = check_league_access(
        league_id=plan.provider_league_id,
        season_year=plan.season_year,
        private_access=private_access,
        http_get=http_get,
    )

    if access_mode is None and result.outcome is EspnLeagueAccessOutcome.PROVIDER_UNAVAILABLE:
        # Timeout, 5xx and 429 say nothing about this user's access.
        raise BackgroundJobExecutionError(
            safe_error_code="provider_unavailable",
            safe_error_message="The fantasy provider is temporarily unavailable.",
            retryable=True,
        )

    if access_mode is not None and result.metadata is not None:
        # A 200 without the League's required fields is not proof (as in import).
        outcome = (
            ReverificationOutcome.PUBLIC
            if access_mode.value == "public"
            else ReverificationOutcome.PRIVATE
        )
    elif result.outcome is EspnLeagueAccessOutcome.AUTHENTICATION_FAILED:
        outcome = ReverificationOutcome.CREDENTIALS_REJECTED
    else:
        outcome = ReverificationOutcome.RECONNECT_REQUIRED

    return _persist(
        session_factory,
        plan=plan,
        outcome=outcome,
        publicly_unreadable=result.outcome is EspnLeagueAccessOutcome.CREDENTIALS_REQUIRED,
        at=now(),
    )


def _prepare(
    session_factory: sessionmaker[Session],
    *,
    user_id: int,
    season_id: int,
    settings: Settings,
    job_authority: SyncJobAuthority | None,
) -> tuple[ReverificationPlan, EspnPrivateAccess | None] | None:
    with session_factory() as session:
        require_job_authority(session, job_authority)
        row = session.execute(
            select(UserLeagueSeasonAccess, Season, League)
            .join(Season, Season.id == UserLeagueSeasonAccess.season_id)
            .join(League, League.id == Season.league_id)
            .join(User, User.id == UserLeagueSeasonAccess.user_id)
            .where(
                UserLeagueSeasonAccess.user_id == user_id,
                UserLeagueSeasonAccess.season_id == season_id,
                UserLeagueSeasonAccess.status.in_(_READABLE),
                User.is_active.is_(True),
                League.provider == FantasyProvider.ESPN,
                League.provider_league_id.is_not(None),
            )
        ).one_or_none()

        if row is None:
            return None

        access, season, league = row
        assert league.provider_league_id is not None
        # Only this user's own connection for this exact target; never selected
        # through the Season's synchronization credential or another user.
        connection = session.scalar(
            select(ProviderConnection)
            .where(
                ProviderConnection.user_id == user_id,
                ProviderConnection.installation_id.is_(None),
                ProviderConnection.provider == FantasyProvider.ESPN,
                ProviderConnection.provider_league_id == league.provider_league_id,
                ProviderConnection.provider_season_year == season.year,
                ProviderConnection.is_active.is_(True),
                ProviderConnection.encrypted_credentials.is_not(None),
                ProviderConnection.credential_version.is_not(None),
                ProviderConnection.last_validation_error.is_(None),
            )
            .order_by(ProviderConnection.id)
            .limit(1)
        )
        private_access = None

        if connection is not None:
            try:
                stored, _ = resolve_connection_credentials(
                    connection=connection, season=season, settings=settings
                )
            except ProviderAdapterResolutionError as exc:
                raise BackgroundJobExecutionError(
                    safe_error_code="provider_credentials_unavailable",
                    safe_error_message="The saved league access could not be used.",
                    retryable=False,
                ) from exc

            private_access = private_access_from_values(
                stored.values["swid"], stored.values["espn_s2"]
            )

        plan = ReverificationPlan(
            access_id=access.id,
            user_id=user_id,
            season_id=season_id,
            provider_league_id=league.provider_league_id,
            season_year=season.year,
            status=access.status,
            proof_mode=access.proof_mode,
            access_connection_id=access.provider_connection_id,
            connection_id=connection.id if connection is not None else None,
            credential_marker=credential_marker(connection) if connection is not None else None,
            job_authority=job_authority,
        )

        return plan, private_access


def _persist(
    session_factory: sessionmaker[Session],
    *,
    plan: ReverificationPlan,
    outcome: ReverificationOutcome,
    publicly_unreadable: bool,
    at: datetime,
) -> ReverificationOutcome:
    with session_factory() as session:
        require_job_authority(session, plan.job_authority)
        # Access row first, then its connection: the lock order of every
        # writer of these rows. A deleted user cascades the row away.
        access = session.scalar(
            select(UserLeagueSeasonAccess)
            .join(User, User.id == UserLeagueSeasonAccess.user_id)
            .where(
                UserLeagueSeasonAccess.id == plan.access_id,
                UserLeagueSeasonAccess.user_id == plan.user_id,
                User.is_active.is_(True),
            )
            .with_for_update(of=UserLeagueSeasonAccess)
            .execution_options(populate_existing=True)
        )
        connection = (
            session.scalar(
                select(ProviderConnection)
                .where(ProviderConnection.id == plan.connection_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if plan.connection_id is not None
            else None
        )

        if (
            access is None
            or access.status is not plan.status
            or access.proof_mode is not plan.proof_mode
            or access.provider_connection_id != plan.access_connection_id
            or (plan.connection_id is not None and connection is None)
            or (
                connection is not None
                and (
                    connection.user_id != plan.user_id
                    or not connection.is_active
                    or credential_marker(connection) != plan.credential_marker
                )
            )
        ):
            session.rollback()
            return ReverificationOutcome.DISCARDED

        if outcome is ReverificationOutcome.PUBLIC:
            _prove(access, UserSeasonProofMode.PUBLIC, None, at)
        elif outcome is ReverificationOutcome.PRIVATE:
            assert connection is not None
            _prove(access, UserSeasonProofMode.PRIVATE_CREDENTIALS, connection.id, at)
            connection.last_validated_at = at
            connection.last_validation_error = None
        else:
            if outcome is ReverificationOutcome.CREDENTIALS_REJECTED:
                assert connection is not None
                connection.last_validation_error = CREDENTIALS_REJECTED_CODE

            access.status = UserSeasonAccessStatus.REVERIFICATION_REQUIRED
            # Explicit: the retry gate is this row's updated_at, which must move
            # even when the status was already reverification_required.
            access.updated_at = at

            if publicly_unreadable and plan.proof_mode is UserSeasonProofMode.PUBLIC:
                _queue_public_proof_peers(session, plan)

        session.commit()

    LOGGER.info("Access reverification outcome: season_id=%s, outcome=%s.", plan.season_id, outcome)

    return outcome


def _prove(
    access: UserLeagueSeasonAccess,
    proof_mode: UserSeasonProofMode,
    connection_id: int | None,
    at: datetime,
) -> None:
    access.status = UserSeasonAccessStatus.ACTIVE
    access.proof_mode = proof_mode
    access.provider_connection_id = connection_id
    access.last_reverified_at = at


def _queue_public_proof_peers(session: Session, plan: ReverificationPlan) -> None:
    """The Season stopped being anonymously readable: every other public-proof user
    proves independently (own credentials or reconnect). No peer is changed here."""

    peers = session.scalars(
        select(UserLeagueSeasonAccess.user_id).where(
            UserLeagueSeasonAccess.season_id == plan.season_id,
            UserLeagueSeasonAccess.user_id != plan.user_id,
            UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
            UserLeagueSeasonAccess.proof_mode == UserSeasonProofMode.PUBLIC,
        )
    ).all()

    for peer_id in peers:
        enqueue_access_reverification(session, user_id=peer_id, season_id=plan.season_id)
