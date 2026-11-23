"""POST /api/onboarding/provider/validate.

Every ESPN interaction is a fake transport; a guard fails the test on any
real outbound request. Credentials are obviously synthetic.
"""

import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app.api.auth_dependencies import get_current_user
from app.api.onboarding import get_espn_http_get
from app.auth.cookies import csrf_cookie_name
from app.auth.security import hmac_identifier
from app.core.settings import get_settings
from app.db.auth_models import RegistrationRateLimit, User, UserRole
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    OnboardingStage,
    ProviderAccessMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.db.session import SessionFactory
from app.main import app
from app.providers.credential_vault import ProviderCredentialError, ProviderCredentialVault
from app.providers.domain import ProviderCredentials
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository

TRUSTED_ORIGIN = "http://localhost:5173"
LEAGUE_ID = "424242"
SEASON = 2024
SYNTHETIC_SWID = "{SYNTHETIC-SWID-0000-TEST}"
SYNTHETIC_S2 = "synthetic-espn-s2-not-a-real-cookie-0001"
WRONG_S2 = "synthetic-wrong-espn-s2-0002"
KEY_V1 = Fernet.generate_key().decode()
KEY_V2 = Fernet.generate_key().decode()
CURRENT_KEY_VERSION = 2
ENDPOINT = "/api/onboarding/provider/validate"


# --------------------------------------------------------------------------
# Fake ESPN transport
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")

        return self._body


def league_body(league_id: str = LEAGUE_ID, season: int = SEASON) -> dict[str, Any]:
    return {
        "id": int(league_id),
        "seasonId": season,
        "settings": {"name": "Synthetic League"},
        # A non-manager member: never an authorization requirement.
        "members": [{"id": "synthetic-member", "isLeagueManager": False}],
    }


@dataclass
class FakeEspn:
    """mode: public | private | timeout | error500 | throttled | missing | malformed."""

    mode: str = "public"
    valid_s2: str = SYNTHETIC_S2
    calls: list[dict[str, Any]] = field(default_factory=list)
    before_respond: Callable[[], None] | None = None

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})

        if self.before_respond is not None:
            self.before_respond()

        cookies = kwargs.get("cookies")
        league_id = url.rstrip("/").split("/")[-1]

        if self.mode == "timeout":
            raise requests.Timeout("SECRET-PROVIDER-DETAIL timed out")

        if self.mode == "connection":
            raise requests.ConnectionError("SECRET-PROVIDER-DETAIL Traceback ESPNAccessDenied")

        if self.mode == "error500":
            return FakeResponse(500, {"messages": ["SECRET-PROVIDER-DETAIL"]})

        if self.mode == "throttled":
            return FakeResponse(429)

        if self.mode == "missing":
            return FakeResponse(404, {"messages": ["SECRET-PROVIDER-DETAIL not found"]})

        if self.mode == "malformed":
            return FakeResponse(200, {"unexpected": "SECRET-PROVIDER-DETAIL"})

        if self.mode == "private" and (cookies is None or cookies.get("espn_s2") != self.valid_s2):
            return FakeResponse(401, {"messages": ["SECRET-PROVIDER-DETAIL"]})

        if "/leagueHistory/" in url:
            return FakeResponse(200, [league_body(league_id)])

        return FakeResponse(200, league_body(league_id))


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Live network requests are forbidden in tests.")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


@pytest.fixture(autouse=True)
def real_authentication() -> Iterator[None]:
    inherited = app.dependency_overrides.pop(get_current_user, None)

    try:
        yield
    finally:
        if inherited is not None:
            app.dependency_overrides[get_current_user] = inherited


@pytest.fixture(autouse=True)
def isolate_rate_limit_rows() -> Iterator[None]:
    with SessionFactory() as session:
        existing = set(session.scalars(select(RegistrationRateLimit.id)).all())

    yield

    with SessionFactory() as session:
        for row in session.scalars(select(RegistrationRateLimit)):
            if row.id not in existing:
                session.delete(row)

        session.commit()


@dataclass
class Limits:
    workspace: int = 50
    source: int = 200


@pytest.fixture
def limits() -> Limits:
    return Limits()


@pytest.fixture
def espn(limits: Limits) -> Iterator[FakeEspn]:
    fake = FakeEspn()
    base = get_settings()

    def settings_override():
        return base.model_copy(
            update={
                "provider_credential_keys": SecretStr(f'{{"1": "{KEY_V1}", "2": "{KEY_V2}"}}'),
                "provider_credential_current_key_version": CURRENT_KEY_VERSION,
                "provider_validation_workspace_maximum_requests": limits.workspace,
                "provider_validation_source_maximum_requests": limits.source,
            }
        )

    app.dependency_overrides[get_espn_http_get] = lambda: fake
    app.dependency_overrides[get_settings] = settings_override

    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_espn_http_get, None)
        app.dependency_overrides.pop(get_settings, None)


@dataclass(frozen=True)
class Member:
    session_token: str
    csrf_token: str
    user_id: int


@dataclass(frozen=True)
class Seeded:
    workspace_id: int
    administrator: Member
    spectator: Member


@pytest.fixture
def seed() -> Iterator[Callable[..., Seeded]]:
    created: list[int] = []

    def create(
        *,
        registration_status: WorkspaceRegistrationStatus | None = (
            WorkspaceRegistrationStatus.PROVISIONAL
        ),
        onboarding_stage: OnboardingStage | None = OnboardingStage.PROVIDER_SETUP_PENDING,
        installation_status: InstallationStatus = InstallationStatus.SETUP_IN_PROGRESS,
        verified: bool = True,
    ) -> Seeded:
        suffix = uuid4().hex[:12]
        slug = f"provider-{suffix}"

        with SessionFactory() as session:
            is_pending = registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
            workspace = Installation(
                application_name="Provider Validation League",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                deletion_requested_at=datetime.now(UTC) if is_pending else None,
                deletion_scheduled_for=(
                    datetime.now(UTC) + timedelta(days=7) if is_pending else None
                ),
                onboarding_stage=onboarding_stage,
            )
            session.add(workspace)
            session.flush()
            created.append(workspace.id)

            repository = AuthenticationRepository(session)
            administrator = repository.create_user(
                username=f"internal-admin-{suffix}",
                display_name="Synthetic Administrator",
                email=f"provider-{suffix}@example.com",
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC) if verified else None
            spectator = repository.create_user(
                username=f"internal-spectator-{suffix}",
                display_name="Synthetic Spectator",
                password="shared spectator passphrase",
                role=UserRole.SPECTATOR,
                workspace_id=workspace.id,
            )
            session.flush()

            sessions = SessionRepository(session)
            admin_session = sessions.create_session(user=administrator, lifetime_hours=1)
            spectator_session = sessions.create_session(user=spectator, lifetime_hours=1)
            session.commit()

            return Seeded(
                workspace_id=workspace.id,
                administrator=Member(
                    admin_session.session_token, admin_session.csrf_token, administrator.id
                ),
                spectator=Member(
                    spectator_session.session_token, spectator_session.csrf_token, spectator.id
                ),
            )

    yield create

    with SessionFactory() as session:
        for workspace_id in created:
            for user in session.scalars(select(User).where(User.workspace_id == workspace_id)):
                session.delete(user)

            session.flush()
            workspace = session.get(Installation, workspace_id)

            if workspace is not None:
                session.delete(workspace)

        session.commit()


def submit(member: Member | None, body: dict[str, Any]):
    with TestClient(app) as client:
        headers = {"Origin": TRUSTED_ORIGIN}

        if member is not None:
            settings = get_settings()
            client.cookies.set(settings.session_cookie_name, member.session_token)
            client.cookies.set(csrf_cookie_name(settings), member.csrf_token)
            headers["X-CSRF-Token"] = member.csrf_token

        return client.post(ENDPOINT, headers=headers, json=body)


def target(**extra: Any) -> dict[str, Any]:
    return {"provider": "espn", "league_id": LEAGUE_ID, "season_year": SEASON, **extra}


def with_credentials(espn_s2: str = SYNTHETIC_S2, **extra: Any) -> dict[str, Any]:
    return target(credentials={"swid": SYNTHETIC_SWID, "espn_s2": espn_s2}, **extra)


def workspace_state(workspace_id: int) -> tuple[Any, ...]:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)
        assert workspace is not None

        return workspace.registration_status, workspace.onboarding_stage, workspace.status


def connections(workspace_id: int) -> list[ProviderConnection]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(ProviderConnection).where(ProviderConnection.installation_id == workspace_id)
            )
        )
        session.expunge_all()

        return rows


def table_counts() -> tuple[int, ...]:
    with SessionFactory() as session:
        return tuple(
            int(session.scalar(select(func.count()).select_from(model)) or 0)
            for model in (League, Season, ProviderLeagueClaim, BackgroundJob)
        )


def assert_no_secret(text: str) -> None:
    for secret in (SYNTHETIC_SWID, SYNTHETIC_SWID.strip("{}"), SYNTHETIC_S2, WRONG_S2):
        assert secret not in text


# --------------------------------------------------------------------------
# Authorization and lifecycle
# --------------------------------------------------------------------------


def test_unauthenticated_request_is_rejected(espn: FakeEspn) -> None:
    response = submit(None, target())

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert espn.calls == []


def test_spectator_cannot_validate(seed: Callable[..., Seeded], espn: FakeEspn) -> None:
    seeded = seed()

    response = submit(seeded.spectator, target())

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "provider_validation_forbidden"
    assert espn.calls == []


def test_unverified_administrator_cannot_validate(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    seeded = seed(verified=False)

    response = submit(seeded.administrator, target())

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert espn.calls == []


@pytest.mark.parametrize(
    ("registration_status", "installation_status", "code"),
    [
        (WorkspaceRegistrationStatus.ACTIVE, InstallationStatus.ACTIVE, "workspace_inactive"),
        (None, InstallationStatus.ACTIVE, "workspace_inactive"),
        (WorkspaceRegistrationStatus.SUSPENDED, InstallationStatus.ACTIVE, "workspace_suspended"),
        (
            WorkspaceRegistrationStatus.DELETION_PENDING,
            InstallationStatus.ACTIVE,
            "workspace_inactive",
        ),
        (WorkspaceRegistrationStatus.DELETED, InstallationStatus.ACTIVE, "workspace_inactive"),
    ],
)
def test_non_provisional_workspaces_are_rejected(
    seed: Callable[..., Seeded],
    espn: FakeEspn,
    registration_status: WorkspaceRegistrationStatus | None,
    installation_status: InstallationStatus,
    code: str,
) -> None:
    seeded = seed(
        registration_status=registration_status,
        installation_status=installation_status,
        onboarding_stage=OnboardingStage.PROVIDER_SETUP_PENDING,
    )

    response = submit(seeded.administrator, target())

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "Workspace access is unavailable.",
        "code": code,
        "retryable": False,
    }
    assert espn.calls == []


@pytest.mark.parametrize(
    "stage",
    sorted(
        set(OnboardingStage)
        - {OnboardingStage.PROVIDER_SETUP_PENDING, OnboardingStage.PROVIDER_VALIDATION_FAILED}
    ),
)
def test_invalid_onboarding_stages_are_rejected(
    seed: Callable[..., Seeded], espn: FakeEspn, stage: OnboardingStage
) -> None:
    seeded = seed(onboarding_stage=stage)

    response = submit(seeded.administrator, target())

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["code"] == "onboarding_stage_invalid"
    assert workspace_state(seeded.workspace_id)[1] is stage
    assert espn.calls == []


@pytest.mark.parametrize(
    "stage",
    [OnboardingStage.PROVIDER_SETUP_PENDING, OnboardingStage.PROVIDER_VALIDATION_FAILED],
)
def test_allowed_stages_accept_validation(
    seed: Callable[..., Seeded], espn: FakeEspn, stage: OnboardingStage
) -> None:
    seeded = seed(onboarding_stage=stage)

    response = submit(seeded.administrator, target())

    assert response.status_code == status.HTTP_200_OK
    assert workspace_state(seeded.workspace_id)[1] is OnboardingStage.LEAGUE_CLAIM_PENDING


@pytest.mark.parametrize(
    "field_name", ["workspace_id", "installation_id", "user_id", "administrator_id"]
)
def test_ownership_fields_in_the_body_are_rejected(
    seed: Callable[..., Seeded], espn: FakeEspn, field_name: str
) -> None:
    seeded = seed()
    other = seed()

    response = submit(seeded.administrator, target(**{field_name: other.workspace_id}))

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert espn.calls == []
    assert connections(other.workspace_id) == []


def test_workspace_is_derived_from_the_session(seed: Callable[..., Seeded], espn: FakeEspn) -> None:
    mine = seed()
    other = seed()

    assert submit(mine.administrator, target()).status_code == status.HTTP_200_OK

    assert len(connections(mine.workspace_id)) == 1
    assert connections(other.workspace_id) == []
    assert workspace_state(other.workspace_id)[1] is OnboardingStage.PROVIDER_SETUP_PENDING


# --------------------------------------------------------------------------
# Local request validation (no ESPN call)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        target(league_id=""),
        target(league_id="abc"),
        target(league_id="12a4"),
        target(league_id="-12"),
        target(league_id="1" * 16),
        target(league_id="0"),
        target(league_id=424242),
        target(season_year=1999),
        target(season_year=datetime.now(UTC).year + 1),
        target(credentials={"swid": SYNTHETIC_SWID}),
        target(credentials={"espn_s2": SYNTHETIC_S2}),
        target(credentials={"swid": "   ", "espn_s2": SYNTHETIC_S2}),
        target(credentials={"swid": SYNTHETIC_SWID, "espn_s2": SYNTHETIC_S2, "extra": "x"}),
    ],
)
def test_malformed_requests_fail_locally(
    seed: Callable[..., Seeded], espn: FakeEspn, body: dict[str, Any]
) -> None:
    seeded = seed()

    response = submit(seeded.administrator, body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert_no_secret(response.text)
    assert espn.calls == []
    assert workspace_state(seeded.workspace_id)[1] is OnboardingStage.PROVIDER_SETUP_PENDING


@pytest.mark.parametrize("provider", ["sleeper", "yahoo", "unknown"])
def test_unsupported_providers_are_rejected(
    seed: Callable[..., Seeded], espn: FakeEspn, provider: str
) -> None:
    seeded = seed()

    for body in (target(provider=provider), with_credentials(provider=provider)):
        response = submit(seeded.administrator, body)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.json()["code"] == "unsupported_provider"
        assert_no_secret(response.text)

    assert espn.calls == []
    assert connections(seeded.workspace_id) == []


# --------------------------------------------------------------------------
# Public leagues
# --------------------------------------------------------------------------


def test_public_league_validates_without_credentials(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    seeded = seed()
    before = table_counts()

    response = submit(seeded.administrator, target())

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "validated",
        "code": "provider_validated",
        "provider": "espn",
        "access_mode": "public",
        "onboarding_stage": "league_claim_pending",
    }
    assert len(espn.calls) == 1
    assert espn.calls[0]["cookies"] is None

    [connection] = connections(seeded.workspace_id)
    assert connection.provider is FantasyProvider.ESPN
    assert connection.is_active is True
    assert connection.access_mode is ProviderAccessMode.PUBLIC
    assert connection.provider_league_id == LEAGUE_ID
    assert connection.provider_season_year == SEASON
    assert connection.encrypted_credentials is None
    assert connection.credential_version is None
    assert connection.last_validated_at is not None
    assert connection.last_validation_error is None

    assert workspace_state(seeded.workspace_id) == (
        WorkspaceRegistrationStatus.PROVISIONAL,
        OnboardingStage.LEAGUE_CLAIM_PENDING,
        InstallationStatus.SETUP_IN_PROGRESS,
    )
    assert table_counts() == before


def test_leading_zeros_are_normalized_in_the_stored_target(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    seeded = seed()

    assert submit(seeded.administrator, target(league_id="000424242")).status_code == 200

    [connection] = connections(seeded.workspace_id)
    assert connection.provider_league_id == LEAGUE_ID


def test_unnecessary_credentials_for_a_public_league_are_never_stored(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    seeded = seed()

    response = submit(seeded.administrator, with_credentials())

    assert response.json()["access_mode"] == "public"
    assert [call["cookies"] for call in espn.calls] == [None]

    [connection] = connections(seeded.workspace_id)
    assert connection.encrypted_credentials is None
    assert connection.credential_version is None


# --------------------------------------------------------------------------
# Private leagues
# --------------------------------------------------------------------------


def test_private_league_reports_credentials_required_without_advancing(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    espn.mode = "private"
    seeded = seed()

    response = submit(seeded.administrator, target())

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "credentials_required",
        "code": "credentials_required",
        "provider": "espn",
        "access_mode": None,
        "onboarding_stage": "provider_setup_pending",
    }
    assert connections(seeded.workspace_id) == []
    assert workspace_state(seeded.workspace_id)[1] is OnboardingStage.PROVIDER_SETUP_PENDING


def test_private_league_with_valid_credentials_is_encrypted_with_current_key(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    espn.mode = "private"
    seeded = seed()
    before = table_counts()

    response = submit(seeded.administrator, with_credentials())

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["access_mode"] == "private"
    assert_no_secret(response.text)
    # Anonymous season + history attempts, then one authenticated request.
    assert [call["cookies"] is None for call in espn.calls] == [True, True, False]

    [connection] = connections(seeded.workspace_id)
    assert connection.access_mode is ProviderAccessMode.PRIVATE
    assert connection.credential_version == CURRENT_KEY_VERSION
    assert connection.encrypted_credentials is not None
    assert_no_secret(connection.encrypted_credentials)

    envelope = ProviderCredentialVault(keys={1: KEY_V1, 2: KEY_V2}).decrypt(
        encrypted_credentials=connection.encrypted_credentials,
        key_version=CURRENT_KEY_VERSION,
        expected_provider=FantasyProvider.ESPN,
        expected_schema_version=connection.credential_schema_version,
    )
    assert dict(envelope.credentials.values) == {"swid": SYNTHETIC_SWID, "espn_s2": SYNTHETIC_S2}

    for value in (
        connection.display_name,
        connection.provider_league_id,
        connection.last_validation_error or "",
    ):
        assert_no_secret(value)

    assert workspace_state(seeded.workspace_id)[1] is OnboardingStage.LEAGUE_CLAIM_PENDING
    assert table_counts() == before


def test_invalid_private_credentials_fail_safely(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    espn.mode = "private"
    seeded = seed()

    response = submit(seeded.administrator, with_credentials(espn_s2=WRONG_S2))

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["code"] == "provider_authentication_failed"
    assert response.json()["retryable"] is False
    assert_no_secret(response.text)
    assert connections(seeded.workspace_id) == []
    assert workspace_state(seeded.workspace_id)[1] is OnboardingStage.PROVIDER_VALIDATION_FAILED


def seed_prior_private_connection(workspace_id: int) -> ProviderConnection:
    ciphertext = ProviderCredentialVault(keys={1: KEY_V1}).encrypt(
        provider=FantasyProvider.ESPN,
        credentials=ProviderCredentials(
            values={"swid": "{PRIOR-SYNTHETIC}", "espn_s2": "prior-synthetic-s2"}
        ),
        key_version=1,
    )

    with SessionFactory() as session:
        connection = ProviderConnection(
            installation_id=workspace_id,
            provider=FantasyProvider.ESPN,
            display_name="ESPN",
            is_active=True,
            access_mode=ProviderAccessMode.PRIVATE,
            provider_league_id="111111",
            provider_season_year=2023,
            encrypted_credentials=ciphertext,
            credential_version=1,
            last_validated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        session.add(connection)
        session.commit()
        session.expunge(connection)

        return connection


def test_failed_replacement_preserves_prior_target_and_ciphertext(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    espn.mode = "private"
    seeded = seed(onboarding_stage=OnboardingStage.PROVIDER_VALIDATION_FAILED)
    prior = seed_prior_private_connection(seeded.workspace_id)

    response = submit(seeded.administrator, with_credentials(espn_s2=WRONG_S2))

    assert response.json()["code"] == "provider_authentication_failed"
    [connection] = connections(seeded.workspace_id)
    assert connection.encrypted_credentials == prior.encrypted_credentials
    assert connection.credential_version == 1
    assert connection.provider_league_id == "111111"
    assert connection.provider_season_year == 2023
    assert connection.last_validated_at == prior.last_validated_at


def test_successful_replacement_uses_current_key_and_keeps_one_connection(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    espn.mode = "private"
    seeded = seed(onboarding_stage=OnboardingStage.PROVIDER_VALIDATION_FAILED)
    prior = seed_prior_private_connection(seeded.workspace_id)

    assert submit(seeded.administrator, with_credentials()).status_code == 200

    [connection] = connections(seeded.workspace_id)
    assert connection.id == prior.id
    assert connection.credential_version == CURRENT_KEY_VERSION
    assert connection.encrypted_credentials != prior.encrypted_credentials
    assert connection.provider_league_id == LEAGUE_ID
    assert connection.provider_season_year == SEASON


def test_public_replacement_of_private_connection_drops_credentials(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    seeded = seed(onboarding_stage=OnboardingStage.PROVIDER_VALIDATION_FAILED)
    seed_prior_private_connection(seeded.workspace_id)

    assert submit(seeded.administrator, target()).json()["access_mode"] == "public"

    [connection] = connections(seeded.workspace_id)
    assert connection.access_mode is ProviderAccessMode.PUBLIC
    assert connection.encrypted_credentials is None
    assert connection.credential_version is None


# --------------------------------------------------------------------------
# Provider failures
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "status_code", "code", "retryable", "failed_stage"),
    [
        ("timeout", 503, "provider_unavailable", True, False),
        ("connection", 503, "provider_unavailable", True, False),
        ("error500", 503, "provider_unavailable", True, False),
        ("throttled", 503, "provider_unavailable", True, False),
        ("missing", 422, "provider_validation_failed", False, True),
        ("malformed", 422, "provider_validation_failed", False, True),
    ],
)
def test_provider_failures_are_safe(
    seed: Callable[..., Seeded],
    espn: FakeEspn,
    mode: str,
    status_code: int,
    code: str,
    retryable: bool,
    failed_stage: bool,
) -> None:
    espn.mode = mode
    seeded = seed()

    response = submit(seeded.administrator, target())

    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert response.json()["retryable"] is retryable
    for marker in ("SECRET-PROVIDER-DETAIL", "Traceback", "ESPNAccessDenied", "Timeout", "lm-api"):
        assert marker not in response.text
    assert connections(seeded.workspace_id) == []
    assert workspace_state(seeded.workspace_id)[1] is (
        OnboardingStage.PROVIDER_VALIDATION_FAILED
        if failed_stage
        else OnboardingStage.PROVIDER_SETUP_PENDING
    )
    assert len(espn.calls) == 1


def test_missing_and_malformed_targets_share_one_response(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    bodies = []

    for mode in ("missing", "malformed"):
        espn.mode = mode
        bodies.append(submit(seed().administrator, target()).json())

    assert bodies[0] == bodies[1]


# --------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------


def test_workspace_limit_blocks_before_espn(
    seed: Callable[..., Seeded], espn: FakeEspn, limits: Limits
) -> None:
    limits.workspace = 3
    espn.mode = "missing"
    seeded = seed()

    for _ in range(3):
        assert submit(seeded.administrator, target()).status_code == 422

    calls_before = len(espn.calls)
    state_before = workspace_state(seeded.workspace_id)
    response = submit(seeded.administrator, target())

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response.json()["code"] == "provider_validation_throttled"
    assert int(response.headers["Retry-After"]) > 0
    assert len(espn.calls) == calls_before
    assert workspace_state(seeded.workspace_id) == state_before
    assert connections(seeded.workspace_id) == []


def test_workspace_buckets_are_isolated(
    seed: Callable[..., Seeded], espn: FakeEspn, limits: Limits
) -> None:
    limits.workspace = 1
    espn.mode = "missing"
    first, second = seed(), seed()

    assert submit(first.administrator, target()).status_code == 422
    assert submit(first.administrator, target()).status_code == 429
    assert submit(second.administrator, target()).status_code == 422


def test_source_limit_is_shared_across_workspaces(
    seed: Callable[..., Seeded], espn: FakeEspn, limits: Limits
) -> None:
    limits.source = 2
    espn.mode = "missing"
    first, second = seed(), seed()

    assert submit(first.administrator, target()).status_code == 422
    assert submit(second.administrator, target()).status_code == 422

    response = submit(second.administrator, target())

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert len(espn.calls) == 2


def test_throttle_identities_exclude_league_and_credentials(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    seeded = seed()
    secret = get_settings().app_secret_key.get_secret_value()

    submit(seeded.administrator, with_credentials())

    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(RegistrationRateLimit).where(
                    RegistrationRateLimit.action.like("provider_validation_%")
                )
            )
        )

    workspace_row = next(row for row in rows if row.action == "provider_validation_workspace")
    assert workspace_row.identity_hash == hmac_identifier(
        f"workspace:{seeded.workspace_id}",
        secret=secret,
        namespace="registration-rate-limit:provider_validation_workspace",
    )

    for row in rows:
        assert_no_secret(row.identity_hash)
        assert LEAGUE_ID not in row.identity_hash
        for candidate in (LEAGUE_ID, SYNTHETIC_SWID, SYNTHETIC_S2):
            assert row.identity_hash != hmac_identifier(
                candidate, secret=secret, namespace=f"registration-rate-limit:{row.action}"
            )


# --------------------------------------------------------------------------
# Transactions and concurrency
# --------------------------------------------------------------------------


def test_encryption_failure_rolls_back_connection_and_stage(
    seed: Callable[..., Seeded], espn: FakeEspn, monkeypatch: pytest.MonkeyPatch
) -> None:
    espn.mode = "private"
    seeded = seed()

    def fail(*args: Any, **kwargs: Any) -> str:
        raise ProviderCredentialError("SECRET-ENCRYPTION-DETAIL")

    monkeypatch.setattr(ProviderCredentialVault, "encrypt", fail)

    response = submit(seeded.administrator, with_credentials())

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "SECRET-ENCRYPTION-DETAIL" not in response.text
    assert connections(seeded.workspace_id) == []
    assert workspace_state(seeded.workspace_id)[1] is OnboardingStage.PROVIDER_SETUP_PENDING


def run_concurrently(*bodies: tuple[Member, dict[str, Any]]) -> list[int]:
    results: list[int] = [0] * len(bodies)

    def worker(index: int, member: Member, body: dict[str, Any]) -> None:
        results[index] = submit(member, body).status_code

    threads = [
        threading.Thread(target=worker, args=(index, member, body))
        for index, (member, body) in enumerate(bodies)
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=30)

    return results


def test_concurrent_successes_leave_one_connection_and_consistent_stage(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    seeded = seed()
    barrier = threading.Barrier(2, timeout=10)
    espn.before_respond = barrier.wait

    statuses = run_concurrently(
        (seeded.administrator, target(league_id="111111")),
        (seeded.administrator, target(league_id="222222")),
    )

    assert sorted(statuses) == [200, 409]
    [connection] = connections(seeded.workspace_id)
    assert connection.provider_league_id in {"111111", "222222"}
    assert workspace_state(seeded.workspace_id)[1] is OnboardingStage.LEAGUE_CLAIM_PENDING


def test_concurrent_success_and_failure_never_lets_failure_win(
    seed: Callable[..., Seeded], espn: FakeEspn
) -> None:
    espn.mode = "private"
    seeded = seed()
    barrier = threading.Barrier(2, timeout=10)
    seen: list[int] = []
    lock = threading.Lock()

    def sync_once() -> None:
        # Synchronize only the first request from each thread.
        with lock:
            seen.append(1)
            first_two = len(seen) <= 2

        if first_two:
            barrier.wait()

    espn.before_respond = sync_once

    statuses = run_concurrently(
        (seeded.administrator, with_credentials()),
        (seeded.administrator, with_credentials(espn_s2=WRONG_S2)),
    )

    assert 200 in statuses
    [connection] = connections(seeded.workspace_id)
    assert connection.access_mode is ProviderAccessMode.PRIVATE
    envelope = ProviderCredentialVault(keys={1: KEY_V1, 2: KEY_V2}).decrypt(
        encrypted_credentials=connection.encrypted_credentials or "",
        key_version=connection.credential_version or 0,
        expected_provider=FantasyProvider.ESPN,
        expected_schema_version=connection.credential_schema_version,
    )
    assert envelope.credentials.values["espn_s2"] == SYNTHETIC_S2
    assert workspace_state(seeded.workspace_id)[1] is OnboardingStage.LEAGUE_CLAIM_PENDING


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------


def test_logs_and_responses_never_contain_credentials(
    seed: Callable[..., Seeded], espn: FakeEspn, caplog: pytest.LogCaptureFixture
) -> None:
    espn.mode = "private"
    caplog.set_level(logging.DEBUG)
    responses = []

    for body in (
        with_credentials(espn_s2=WRONG_S2),
        target(credentials={"swid": SYNTHETIC_SWID, "espn_s2": "x" * 2000}),
        with_credentials(),
    ):
        responses.append(submit(seed().administrator, body).text)

    assert_no_secret(caplog.text)
    assert "x" * 100 not in caplog.text

    for text in responses:
        assert_no_secret(text)
