"""Personal League import: independent provider proof, then one atomic import (ADR 0010).

Sequence (no provider network call runs inside a database transaction):

T0  Short committed transaction: check caller eligibility and count the
    attempt in the persistent throttle. A failed proof still consumes quota.
Proof  No Session open. ESPN anonymous first; only this request's own
    credentials if ESPN requires authentication. Proof never reads canonical
    League or Season rows, claims, other users' access or connections, or
    cached data, so a failure cannot reveal whether the League exists.
T1  One transaction, lock order:
    caller's workspace (share, transitional) -> User -> the League's
    claimed workspace (key share, transitional) -> League -> Season (via
    synchronization enqueue).
    Recheck eligibility; create or update this user's own connection for
    private proof; insert or retrieve League and Season through database
    uniqueness; clear ``orphaned_at`` under the League lock; create or
    reactivate this user's access; enqueue Season synchronization unless the
    Season is fresh; set the default Season only when absent; commit.

Transitional eligibility until the identity cutover: an active, verified
administrator in an active workspace with a standard session. The access
belongs to the User, never to the workspace, and no ProviderLeagueClaim is
read for authorization, created or changed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.auth_models import AuthSession, AuthSessionPurpose, User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League
from app.db.platform_enums import (
    FantasyProvider,
    ProviderAccessMode,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import Installation, ProviderConnection
from app.providers.credential_keyring import ProviderCredentialKeyring
from app.providers.credential_vault import CREDENTIAL_SCHEMA_VERSION
from app.providers.espn.league_access import (
    EspnLeagueAccessOutcome,
    EspnPrivateAccess,
    HttpGet,
)
from app.repositories.league_access_repository import LeagueAccessRepository
from app.repositories.provider_league_claim_repository import workspace_league_condition
from app.repositories.registration_rate_limit_repository import (
    RegistrationRateLimitRepository,
)
from app.services.provider_onboarding import (
    ESPN_CONNECTION_DISPLAY_NAME,
    ProviderOnboardingError,
    check_league_access,
    encrypt_espn_credentials,
    failure_for,
)
from app.services.season_sync_jobs import enqueue_season_sync
from app.services.season_sync_state import has_successful_data
from app.services.workspace_lifecycle import is_active_workspace

USER_THROTTLE_ACTION = "league_import_user"
SOURCE_THROTTLE_ACTION = "league_import_source"
UNKNOWN_SOURCE = "unknown-source"


def _error(
    status_code: int,
    code: str,
    detail: str,
    *,
    retryable: bool,
    retry_after_seconds: int | None = None,
) -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=status_code,
        code=code,
        detail=detail,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
    )


def import_forbidden() -> ProviderOnboardingError:
    return _error(
        403,
        "league_import_forbidden",
        "This account cannot import leagues yet.",
        retryable=False,
    )


def credentials_required() -> ProviderOnboardingError:
    return _error(
        422,
        "provider_credentials_required",
        "ESPN requires private league credentials. Add SWID and espn_s2 and try again.",
        retryable=False,
    )


def import_temporarily_unavailable() -> ProviderOnboardingError:
    return _error(
        503,
        "league_import_unavailable",
        "The league could not be imported right now. Try again in a moment.",
        retryable=True,
    )


@dataclass(frozen=True, slots=True)
class VerifiedLeagueImport:
    """Detached, immutable result of a successful provider proof.

    Every field came from the provider response for this exact target.
    ``private_access`` stays in process memory only until T1 encrypts it;
    it is set only for private proof and never appears in repr.
    """

    provider: FantasyProvider
    provider_league_id: str
    season_year: int
    league_name: str
    current_week: int
    regular_season_weeks: int
    proof_mode: UserSeasonProofMode
    private_access: EspnPrivateAccess | None

    def __repr__(self) -> str:
        return (
            f"VerifiedLeagueImport(provider={self.provider.value}, "
            f"provider_league_id={self.provider_league_id}, season_year={self.season_year}, "
            f"proof_mode={self.proof_mode.value})"
        )


@dataclass(frozen=True, slots=True)
class LeagueImportResult:
    """Safe import outcome; no user, connection, access-row or job identifiers."""

    provider_league_id: str
    league_name: str
    season_id: int
    season_year: int
    sync_state: SeasonSyncState
    data_available: bool
    sync_queued: bool


def import_league(
    session_factory: Callable[[], Session],
    *,
    user_id: int,
    session_id: int,
    source: str | None,
    provider_league_id: str,
    season_year: int,
    private_access: EspnPrivateAccess | None,
    credential_keyring: ProviderCredentialKeyring,
    throttle_secret: str,
    user_maximum: int,
    source_maximum: int,
    window_minutes: int,
    http_get: HttpGet | None = None,
    now: datetime | None = None,
) -> LeagueImportResult:
    """Throttle, prove ESPN access with no transaction open, then import atomically."""

    with session_factory() as session:
        _require_eligible(session, user_id=user_id, session_id=session_id, lock=False)

        try:
            _record_attempt(
                session,
                user_id=user_id,
                source=source or UNKNOWN_SOURCE,
                throttle_secret=throttle_secret,
                user_maximum=user_maximum,
                source_maximum=source_maximum,
                window_minutes=window_minutes,
            )
        finally:
            # Persist the counted attempt even when it is over the limit.
            session.commit()

    verified = prove_espn_league_access(
        provider_league_id=provider_league_id,
        season_year=season_year,
        private_access=private_access,
        http_get=http_get,
    )

    with session_factory() as session:
        try:
            result = persist_verified_import(
                session,
                user_id=user_id,
                session_id=session_id,
                verified=verified,
                credential_keyring=credential_keyring,
                now=now or datetime.now(UTC),
            )
            session.commit()
        except OperationalError:
            # Deadlock or lock failure: nothing from this request persists.
            session.rollback()
            raise import_temporarily_unavailable() from None
        except Exception:
            session.rollback()
            raise

    return result


# --------------------------------------------------------------------------
# Eligibility and throttle
# --------------------------------------------------------------------------


def _require_eligible(session: Session, *, user_id: int, session_id: int, lock: bool) -> None:
    """Transitional caller eligibility; the target is any verified personal account."""

    workspace_id = session.scalar(select(User.workspace_id).where(User.id == user_id))
    workspace_query = select(Installation).where(Installation.id == workspace_id)
    user_query = select(User).where(User.id == user_id)

    if lock:
        # Workspace before User: the order workspace deletion uses (it locks
        # the workspace, then cascades users). Share blocks a lifecycle change
        # until this import commits.
        workspace_query = workspace_query.with_for_update(read=True)
        user_query = user_query.with_for_update()

    workspace = session.scalar(workspace_query) if workspace_id is not None else None
    user = session.scalar(user_query.execution_options(populate_existing=True))
    auth_session = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == session_id)
        .execution_options(populate_existing=True)
    )

    if (
        user is None
        or not user.is_active
        or user.email_verified_at is None
        or user.role is not UserRole.ADMIN
        or auth_session is None
        or auth_session.user_id != user_id
        or auth_session.purpose is not AuthSessionPurpose.STANDARD
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= datetime.now(UTC)
        or not is_active_workspace(workspace)
    ):
        raise import_forbidden()


def _record_attempt(
    session: Session,
    *,
    user_id: int,
    source: str,
    throttle_secret: str,
    user_maximum: int,
    source_maximum: int,
    window_minutes: int,
) -> None:
    """Count one attempt per user and per client address; raise when either is spent.

    Identities are HMAC-hashed by the repository and never include
    credentials or email. The per-user bucket bounds every target, so a
    per-target bucket under the same limit would add nothing.
    """

    limiter = RegistrationRateLimitRepository(session, secret=throttle_secret)
    user_limit = limiter.record_request(
        action=USER_THROTTLE_ACTION,
        identity=f"user:{user_id}",
        maximum_requests=user_maximum,
        window_minutes=window_minutes,
    )
    source_limit = limiter.record_request(
        action=SOURCE_THROTTLE_ACTION,
        identity=source,
        maximum_requests=source_maximum,
        window_minutes=window_minutes,
    )

    if not user_limit.allowed or not source_limit.allowed:
        raise _error(
            429,
            "league_import_throttled",
            "Too many league import attempts. Try again later.",
            retryable=True,
            retry_after_seconds=max(
                user_limit.retry_after_seconds, source_limit.retry_after_seconds, 1
            ),
        )


# --------------------------------------------------------------------------
# Provider proof (no database access)
# --------------------------------------------------------------------------


def prove_espn_league_access(
    *,
    provider_league_id: str,
    season_year: int,
    private_access: EspnPrivateAccess | None,
    http_get: HttpGet | None,
) -> VerifiedLeagueImport:
    """Prove access using only anonymous access or this request's credentials.

    Every failure is a provider-derived classification, identical whether or
    not a canonical League exists locally.
    """

    result, access_mode = check_league_access(
        league_id=provider_league_id,
        season_year=season_year,
        private_access=private_access,
        http_get=http_get,
    )

    if result.outcome is EspnLeagueAccessOutcome.CREDENTIALS_REQUIRED:
        raise credentials_required()

    if access_mode is None:
        raise failure_for(result.outcome)

    if result.metadata is None:
        # Never invent League or Season metadata to satisfy the schema.
        raise failure_for(EspnLeagueAccessOutcome.TARGET_UNAVAILABLE)

    private = access_mode is ProviderAccessMode.PRIVATE

    return VerifiedLeagueImport(
        provider=FantasyProvider.ESPN,
        provider_league_id=provider_league_id,
        season_year=season_year,
        league_name=result.metadata.name,
        current_week=result.metadata.current_week,
        regular_season_weeks=result.metadata.regular_season_weeks,
        proof_mode=(
            UserSeasonProofMode.PRIVATE_CREDENTIALS if private else UserSeasonProofMode.PUBLIC
        ),
        private_access=private_access if private else None,
    )


# --------------------------------------------------------------------------
# T1: atomic import
# --------------------------------------------------------------------------


def persist_verified_import(
    session: Session,
    *,
    user_id: int,
    session_id: int,
    verified: VerifiedLeagueImport,
    credential_keyring: ProviderCredentialKeyring,
    now: datetime,
) -> LeagueImportResult:
    """Apply one verified import in the caller's transaction; the caller commits."""

    _require_eligible(session, user_id=user_id, session_id=session_id, lock=True)

    connection: ProviderConnection | None = None

    if verified.proof_mode is UserSeasonProofMode.PRIVATE_CREDENTIALS:
        connection = _upsert_user_connection(
            session,
            user_id=user_id,
            verified=verified,
            credential_keyring=credential_keyring,
            now=now,
        )

    repository = LeagueAccessRepository(session)
    _key_share_lock_claimed_workspace(session, verified)
    league = repository.lock_or_create_league(
        provider=verified.provider,
        provider_league_id=verified.provider_league_id,
        name=verified.league_name,
    )
    # Revival under the League lock: a purge that locks next sees active access.
    league.orphaned_at = None

    season = repository.create_or_get_season(
        league_id=league.id,
        year=verified.season_year,
        current_week=verified.current_week,
        regular_season_weeks=verified.regular_season_weeks,
    )
    _activate_access(
        session,
        repository,
        user_id=user_id,
        season_id=season.id,
        verified=verified,
        connection=connection,
        now=now,
    )

    # Fresh data is reused as is; every other state (including legacy NULL)
    # gets one active job, or reuses the one already queued or running.
    sync_queued = season.sync_state is not SeasonSyncState.FRESH

    if sync_queued:
        enqueue_season_sync(session, season_id=season.id)

    preferences = repository.get_or_create_preferences(user_id=user_id)

    if preferences.default_league_season_id is None:
        preferences.default_league_season_id = season.id

    session.flush()

    if season.sync_state is None:  # pragma: no cover - enqueue always sets a state
        raise RuntimeError("Season synchronization state is missing after import.")

    return LeagueImportResult(
        provider_league_id=verified.provider_league_id,
        league_name=league.name,
        season_id=season.id,
        season_year=season.year,
        sync_state=season.sync_state,
        data_available=has_successful_data(season),
        sync_queued=sync_queued,
    )


def _upsert_user_connection(
    session: Session,
    *,
    user_id: int,
    verified: VerifiedLeagueImport,
    credential_keyring: ProviderCredentialKeyring,
    now: datetime,
) -> ProviderConnection:
    """Create or update this user's own connection for this exact target.

    Ownership is ``user_id`` only (no workspace). The locked User row
    serializes this lookup, so one user holds at most one active connection
    per target. Another user's connection is never read, whatever its
    credentials; plaintext credentials are never compared.
    """

    if verified.private_access is None:
        raise ValueError("Private proof requires the request's credentials.")

    encrypted, key_version = encrypt_espn_credentials(verified.private_access, credential_keyring)

    connection = session.scalar(
        select(ProviderConnection)
        .where(
            ProviderConnection.user_id == user_id,
            ProviderConnection.installation_id.is_(None),
            ProviderConnection.provider == verified.provider,
            ProviderConnection.provider_league_id == verified.provider_league_id,
            ProviderConnection.provider_season_year == verified.season_year,
            ProviderConnection.is_active.is_(True),
        )
        .order_by(ProviderConnection.id)
        .limit(1)
    )

    if connection is None:
        connection = ProviderConnection(
            user_id=user_id,
            provider=verified.provider,
            is_active=True,
        )
        session.add(connection)

    connection.display_name = ESPN_CONNECTION_DISPLAY_NAME
    connection.provider_league_id = verified.provider_league_id
    connection.provider_season_year = verified.season_year
    connection.access_mode = ProviderAccessMode.PRIVATE
    connection.encrypted_credentials = encrypted
    connection.credential_version = key_version
    connection.credential_schema_version = CREDENTIAL_SCHEMA_VERSION
    connection.last_validated_at = now
    connection.last_validation_error = None
    session.flush()

    return connection


def _key_share_lock_claimed_workspace(session: Session, verified: VerifiedLeagueImport) -> None:
    """Transitional: lock a claimed League's workspace before the League row.

    Synchronization enqueue takes this lock before the Season lock, and
    workspace deletion takes the workspace before its Leagues; taking it
    first keeps this transaction in the same order.
    """

    workspace_id = session.scalar(
        select(League.installation_id).where(
            League.provider == verified.provider,
            League.provider_league_id == verified.provider_league_id,
            workspace_league_condition(League.installation_id),
        )
    )

    if workspace_id is not None:
        session.scalar(
            select(Installation.id)
            .where(Installation.id == workspace_id)
            .with_for_update(read=True, key_share=True)
        )


def _activate_access(
    session: Session,
    repository: LeagueAccessRepository,
    *,
    user_id: int,
    season_id: int,
    verified: VerifiedLeagueImport,
    connection: ProviderConnection | None,
    now: datetime,
) -> None:
    """Create, re-prove or reactivate this user's access after successful proof."""

    connection_id = connection.id if connection is not None else None

    # Private access may reference only this user's own connection for the
    # same provider (the schema enforces the proof/connection pairing).
    if (verified.proof_mode is UserSeasonProofMode.PRIVATE_CREDENTIALS) != (
        connection is not None
    ) or (
        connection is not None
        and (connection.user_id != user_id or connection.provider is not verified.provider)
    ):
        raise ValueError("Private access requires the requesting user's own connection.")

    # The locked User row serializes this user's imports, so this read is stable.
    existing = session.scalar(
        select(UserLeagueSeasonAccess).where(
            UserLeagueSeasonAccess.user_id == user_id,
            UserLeagueSeasonAccess.season_id == season_id,
        )
    )

    if existing is None:
        repository.create_or_get_access(
            user_id=user_id,
            season_id=season_id,
            proof_mode=verified.proof_mode,
            provider_connection_id=connection_id,
            verified_at=now,
        )
        return

    # Active, reverification_required and revoked all return to active only
    # because this request just proved access independently. A public proof
    # drops the connection reference but never deletes the user's connection.
    existing.status = UserSeasonAccessStatus.ACTIVE
    existing.proof_mode = verified.proof_mode
    existing.provider_connection_id = connection_id
    existing.last_reverified_at = now
