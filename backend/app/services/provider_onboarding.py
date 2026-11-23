"""Provider selection and league-access validation for provisional onboarding.

Coordinates authorization, throttling, the ESPN access check, encryption,
persistence and the onboarding transition. ESPN request and response
details stay in ``app.providers.espn.league_access``.

Sequence (ADR 0006: no database transaction is held during the network
call):

1. Short transaction: authorize and record both throttle buckets.
2. ESPN check outside any transaction: anonymous first; credentials are
   used only when ESPN clearly requires authentication.
3. Short transaction with the workspace row locked: re-authorize, then
   persist the validated target and advance, or record the failure.

Validation never creates a League, Season, provider-league claim or job,
never activates the workspace and never uses environment credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.platform_enums import (
    FantasyProvider,
    OnboardingStage,
    ProviderAccessMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation, ProviderConnection
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
    ProviderCredentialKeyringConfigurationError,
)
from app.providers.credential_vault import (
    CREDENTIAL_SCHEMA_VERSION,
    ProviderCredentialError,
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.providers.espn.league_access import (
    EspnLeagueAccessOutcome,
    EspnPrivateAccess,
    HttpGet,
    check_espn_league_access,
    normalize_swid,
)
from app.repositories.registration_rate_limit_repository import (
    RegistrationRateLimitRepository,
)

PROVIDER_VALIDATION_STAGES = frozenset(
    {
        OnboardingStage.PROVIDER_SETUP_PENDING,
        OnboardingStage.PROVIDER_VALIDATION_FAILED,
    }
)
WORKSPACE_THROTTLE_ACTION = "provider_validation_workspace"
SOURCE_THROTTLE_ACTION = "provider_validation_source"
ESPN_CONNECTION_DISPLAY_NAME = "ESPN"


class ProviderOnboardingError(Exception):
    """Stable, safe onboarding failure. Messages never include input."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class ProviderWorkspaceAccessError(Exception):
    """The workspace is suspended or otherwise not provisional."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _error(status_code: int, code: str, detail: str, *, retryable: bool) -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=status_code, code=code, detail=detail, retryable=retryable
    )


def onboarding_stage_invalid() -> ProviderOnboardingError:
    return _error(
        409,
        "onboarding_stage_invalid",
        "League access cannot be checked at this onboarding step.",
        retryable=False,
    )


def unsupported_provider() -> ProviderOnboardingError:
    return _error(
        422,
        "unsupported_provider",
        "Only ESPN leagues can be connected right now.",
        retryable=False,
    )


AUTHENTICATION_FAILED = (
    422,
    "provider_authentication_failed",
    "ESPN did not accept these private league credentials. Check SWID and espn_s2 and try again.",
    False,
)
VALIDATION_FAILED = (
    422,
    "provider_validation_failed",
    "Fantasy What-If could not read that ESPN league. Check the League ID and season year "
    "and try again.",
    False,
)
PROVIDER_UNAVAILABLE = (
    503,
    "provider_unavailable",
    "ESPN is not responding right now. Try again in a few minutes.",
    True,
)
ENCRYPTION_UNAVAILABLE = (
    503,
    "provider_validation_failed",
    "League access could not be saved right now. Try again later.",
    True,
)


@dataclass(frozen=True, slots=True)
class ProviderValidationOutcome:
    credentials_required: bool
    access_mode: ProviderAccessMode | None
    onboarding_stage: OnboardingStage


def authorize_provider_validation(
    session: Session,
    *,
    user_id: int,
    workspace_id: int,
    lock: bool = False,
) -> Installation:
    """Require a verified administrator of a provisional workspace at an allowed stage."""

    user = session.get(User, user_id)

    if (
        user is None
        or user.workspace_id != workspace_id
        or user.role is not UserRole.ADMIN
        or user.email_verified_at is None
        or not user.is_active
    ):
        raise _error(
            403,
            "provider_validation_forbidden",
            "Only a verified workspace administrator can connect a league.",
            retryable=False,
        )

    query = select(Installation).where(Installation.id == workspace_id)

    if lock:
        query = query.with_for_update()

    workspace = session.scalar(query)

    if workspace is None:
        raise ProviderWorkspaceAccessError("workspace_inactive")

    if workspace.registration_status is WorkspaceRegistrationStatus.SUSPENDED:
        raise ProviderWorkspaceAccessError("workspace_suspended")

    if workspace.registration_status is not WorkspaceRegistrationStatus.PROVISIONAL:
        raise ProviderWorkspaceAccessError("workspace_inactive")

    if workspace.onboarding_stage not in PROVIDER_VALIDATION_STAGES:
        raise onboarding_stage_invalid()

    return workspace


def record_validation_attempt(
    session: Session,
    *,
    workspace_id: int,
    source: str,
    throttle_secret: str,
    workspace_maximum: int,
    source_maximum: int,
    window_minutes: int,
) -> None:
    """Count one attempt in both buckets; raise when either is exhausted.

    Identities are the workspace ID and the normalized request source,
    HMAC-hashed by the repository. League IDs and credentials are never
    part of a throttle identity.
    """

    limiter = RegistrationRateLimitRepository(session, secret=throttle_secret)
    workspace_limit = limiter.record_request(
        action=WORKSPACE_THROTTLE_ACTION,
        identity=f"workspace:{workspace_id}",
        maximum_requests=workspace_maximum,
        window_minutes=window_minutes,
    )
    source_limit = limiter.record_request(
        action=SOURCE_THROTTLE_ACTION,
        identity=source,
        maximum_requests=source_maximum,
        window_minutes=window_minutes,
    )

    if not workspace_limit.allowed or not source_limit.allowed:
        raise ProviderOnboardingError(
            status_code=429,
            code="provider_validation_throttled",
            detail="Too many league access checks. Try again later.",
            retryable=True,
            retry_after_seconds=max(
                workspace_limit.retry_after_seconds,
                source_limit.retry_after_seconds,
                1,
            ),
        )


def check_league_access(
    *,
    league_id: str,
    season_year: int,
    private_access: EspnPrivateAccess | None,
    http_get: HttpGet | None,
) -> tuple[EspnLeagueAccessOutcome, ProviderAccessMode | None]:
    """Anonymous first; submitted credentials are used only if ESPN requires them."""

    anonymous = check_espn_league_access(
        league_id=league_id,
        season_year=season_year,
        http_get=http_get,
    )

    if anonymous.accessible:
        # Public: any submitted credentials are discarded unused.
        return anonymous.outcome, ProviderAccessMode.PUBLIC

    if (
        anonymous.outcome is not EspnLeagueAccessOutcome.CREDENTIALS_REQUIRED
        or private_access is None
    ):
        return anonymous.outcome, None

    authenticated = check_espn_league_access(
        league_id=league_id,
        season_year=season_year,
        private_access=private_access,
        http_get=http_get,
    )

    if authenticated.accessible:
        return authenticated.outcome, ProviderAccessMode.PRIVATE

    return authenticated.outcome, None


def persist_validated_target(
    session: Session,
    *,
    workspace: Installation,
    league_id: str,
    season_year: int,
    access_mode: ProviderAccessMode,
    private_access: EspnPrivateAccess | None,
    credential_keyring: ProviderCredentialKeyring,
    now: datetime,
) -> None:
    """Create or replace the single current connection and advance onboarding.

    The caller holds the workspace row lock and commits. Any failure here,
    including encryption, leaves both the connection and stage unchanged.
    """

    encrypted: str | None = None
    key_version: int | None = None

    if access_mode is not ProviderAccessMode.PRIVATE:
        private_access = None
    else:
        if private_access is None:
            raise ValueError("Private access requires credentials.")

        if not credential_keyring.configured:
            raise _from_spec(ENCRYPTION_UNAVAILABLE)

        key_version = credential_keyring.current_version
        encrypted = ProviderCredentialVault(keys=credential_keyring.keys).encrypt(
            provider=FantasyProvider.ESPN,
            credentials=ProviderCredentials(
                values={"swid": private_access.swid, "espn_s2": private_access.espn_s2}
            ),
            key_version=key_version,
        )

    connection = session.scalar(
        select(ProviderConnection).where(
            ProviderConnection.installation_id == workspace.id,
            ProviderConnection.is_active.is_(True),
        )
    )

    if connection is None:
        connection = ProviderConnection(installation_id=workspace.id, is_active=True)
        session.add(connection)

    connection.provider = FantasyProvider.ESPN
    connection.display_name = ESPN_CONNECTION_DISPLAY_NAME
    connection.provider_league_id = league_id
    connection.provider_season_year = season_year
    connection.access_mode = access_mode
    connection.encrypted_credentials = encrypted
    connection.credential_version = key_version
    connection.credential_schema_version = CREDENTIAL_SCHEMA_VERSION
    connection.last_validated_at = now
    connection.last_validation_error = None

    workspace.onboarding_stage = OnboardingStage.LEAGUE_CLAIM_PENDING
    session.flush()


def private_access_from_values(swid: str, espn_s2: str) -> EspnPrivateAccess:
    return EspnPrivateAccess(swid=normalize_swid(swid), espn_s2=espn_s2.strip())


def _from_spec(spec: tuple[int, str, str, bool]) -> ProviderOnboardingError:
    return _error(spec[0], spec[1], spec[2], retryable=spec[3])


def failure_for(outcome: EspnLeagueAccessOutcome) -> ProviderOnboardingError:
    if outcome is EspnLeagueAccessOutcome.AUTHENTICATION_FAILED:
        return _from_spec(AUTHENTICATION_FAILED)

    if outcome is EspnLeagueAccessOutcome.PROVIDER_UNAVAILABLE:
        return _from_spec(PROVIDER_UNAVAILABLE)

    return _from_spec(VALIDATION_FAILED)


# Failures caused by the submitted target or credentials move onboarding to
# the recoverable provider_validation_failed stage (ADR 0006). A transient
# ESPN outage is not the administrator's error and leaves the stage as is.
STAGE_FAILING_OUTCOMES = frozenset(
    {
        EspnLeagueAccessOutcome.AUTHENTICATION_FAILED,
        EspnLeagueAccessOutcome.TARGET_UNAVAILABLE,
    }
)


def run_provider_validation(
    session_factory: Callable[[], Session],
    *,
    user_id: int,
    workspace_id: int,
    source: str,
    league_id: str,
    season_year: int,
    private_access: EspnPrivateAccess | None,
    credential_keyring: ProviderCredentialKeyring,
    throttle_secret: str,
    workspace_maximum: int,
    source_maximum: int,
    window_minutes: int,
    http_get: HttpGet | None = None,
    now: datetime | None = None,
) -> ProviderValidationOutcome:
    """Authorize, throttle, check ESPN, then persist atomically."""

    with session_factory() as session:
        authorize_provider_validation(session, user_id=user_id, workspace_id=workspace_id)

        try:
            record_validation_attempt(
                session,
                workspace_id=workspace_id,
                source=source,
                throttle_secret=throttle_secret,
                workspace_maximum=workspace_maximum,
                source_maximum=source_maximum,
                window_minutes=window_minutes,
            )
        finally:
            # Persist the counted attempt even when it is over the limit.
            session.commit()

    outcome, access_mode = check_league_access(
        league_id=league_id,
        season_year=season_year,
        private_access=private_access,
        http_get=http_get,
    )

    if outcome is EspnLeagueAccessOutcome.CREDENTIALS_REQUIRED:
        with session_factory() as session:
            workspace = authorize_provider_validation(
                session, user_id=user_id, workspace_id=workspace_id
            )
            stage = workspace.onboarding_stage

        if stage is None:
            raise onboarding_stage_invalid()

        return ProviderValidationOutcome(
            credentials_required=True,
            access_mode=None,
            onboarding_stage=stage,
        )

    if access_mode is None:
        if outcome in STAGE_FAILING_OUTCOMES:
            with session_factory() as session:
                try:
                    workspace = authorize_provider_validation(
                        session, user_id=user_id, workspace_id=workspace_id, lock=True
                    )
                except ProviderOnboardingError, ProviderWorkspaceAccessError:
                    # A concurrent success already advanced: never undo it.
                    workspace = None

                if workspace is not None:
                    workspace.onboarding_stage = OnboardingStage.PROVIDER_VALIDATION_FAILED
                    session.commit()

        raise failure_for(outcome)

    with session_factory() as session:
        try:
            workspace = authorize_provider_validation(
                session, user_id=user_id, workspace_id=workspace_id, lock=True
            )
            persist_validated_target(
                session,
                workspace=workspace,
                league_id=league_id,
                season_year=season_year,
                access_mode=access_mode,
                # Ignored unless access is private, so public stores none.
                private_access=private_access,
                credential_keyring=credential_keyring,
                now=now or datetime.now(UTC),
            )
        except (ProviderCredentialError, ProviderCredentialKeyringConfigurationError) as exc:
            session.rollback()
            raise _from_spec(ENCRYPTION_UNAVAILABLE) from exc
        except Exception:
            session.rollback()
            raise

        session.commit()

    return ProviderValidationOutcome(
        credentials_required=False,
        access_mode=access_mode,
        onboarding_stage=OnboardingStage.LEAGUE_CLAIM_PENDING,
    )
