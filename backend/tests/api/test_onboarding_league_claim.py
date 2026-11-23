"""GET /api/onboarding/provider/target and POST /api/onboarding/provider/claim.

No test makes an ESPN request: claiming never contacts the provider, and
a guard fails any outbound request. Credentials are synthetic.
"""

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.api.auth_dependencies import get_current_user
from app.auth.cookies import csrf_cookie_name
from app.core.settings import get_settings
from app.db.auth_models import (
    AuthSession,
    EmailVerificationToken,
    PasswordResetToken,
    User,
    UserRole,
)
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
from app.db.session import SessionFactory, engine
from app.main import app
from app.providers.credential_vault import ProviderCredentialVault
from app.providers.domain import ProviderCredentials
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository
from app.services.email_verification import issue_email_verification_token
from app.services.password_recovery import issue_password_reset_token

TRUSTED_ORIGIN = "http://localhost:5173"
TARGET = "/api/onboarding/provider/target"
CLAIM = "/api/onboarding/provider/claim"
SYNTHETIC_SWID = "{SYNTHETIC-SWID-CLAIM-TEST}"
SYNTHETIC_S2 = "synthetic-espn-s2-claim-test-not-real"
CONFLICT = {
    "detail": (
        "This fantasy league is already connected to Fantasy What-If. Contact the league "
        "owner and ask for the workspace's spectator access if you need viewing access."
    ),
    "code": "provider_league_already_claimed",
    "retryable": False,
}


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


@dataclass(frozen=True)
class Member:
    session_token: str
    csrf_token: str
    user_id: int


@dataclass(frozen=True)
class Seeded:
    workspace_id: int
    slug: str
    admin_email: str
    administrator: Member
    spectator: Member


@pytest.fixture
def seed() -> Iterator[Callable[..., Seeded]]:
    created: list[int] = []

    def create(
        *,
        league_id: str | None = None,
        access_mode: ProviderAccessMode | None = ProviderAccessMode.PUBLIC,
        onboarding_stage: OnboardingStage = OnboardingStage.LEAGUE_CLAIM_PENDING,
        registration_status: WorkspaceRegistrationStatus = WorkspaceRegistrationStatus.PROVISIONAL,
        installation_status: InstallationStatus = InstallationStatus.SETUP_IN_PROGRESS,
        verified: bool = True,
        with_tokens: bool = False,
    ) -> Seeded:
        suffix = uuid4().hex[:12]
        slug = f"claim-{suffix}"
        email = f"claim-{suffix}@example.com"

        with SessionFactory() as session:
            workspace = Installation(
                application_name="Claim League",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                onboarding_stage=onboarding_stage,
            )
            session.add(workspace)
            session.flush()
            created.append(workspace.id)

            if access_mode is not None:
                encrypted = None
                key_version = None

                if access_mode is ProviderAccessMode.PRIVATE:
                    key_version = 1
                    encrypted = ProviderCredentialVault(
                        keys={1: Fernet.generate_key().decode()}
                    ).encrypt(
                        provider=FantasyProvider.ESPN,
                        credentials=ProviderCredentials(
                            values={"swid": SYNTHETIC_SWID, "espn_s2": SYNTHETIC_S2}
                        ),
                        key_version=1,
                    )

                session.add(
                    ProviderConnection(
                        installation_id=workspace.id,
                        provider=FantasyProvider.ESPN,
                        display_name="ESPN",
                        is_active=True,
                        access_mode=access_mode,
                        provider_league_id=league_id or str(uuid4().int)[:9],
                        provider_season_year=2024,
                        encrypted_credentials=encrypted,
                        credential_version=key_version,
                        last_validated_at=datetime.now(UTC),
                    )
                )

            repository = AuthenticationRepository(session)
            administrator = repository.create_user(
                username=f"internal-admin-{suffix}",
                display_name="Claim Administrator",
                email=email,
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC) if verified else None
            spectator = repository.create_user(
                username=f"internal-spectator-{suffix}",
                display_name="Claim Spectator",
                password="shared spectator passphrase",
                role=UserRole.SPECTATOR,
                workspace_id=workspace.id,
            )
            session.flush()

            if with_tokens:
                issue_email_verification_token(session, user=administrator, lifetime_hours=1)
                issue_password_reset_token(session, user=administrator, lifetime_minutes=30)

            sessions = SessionRepository(session)
            admin_session = sessions.create_session(user=administrator, lifetime_hours=1)
            spectator_session = sessions.create_session(user=spectator, lifetime_hours=1)
            session.commit()

            return Seeded(
                workspace_id=workspace.id,
                slug=slug,
                admin_email=email,
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
            session.execute(text("DELETE FROM installations WHERE id = :id"), {"id": workspace_id})

        session.commit()


def client_for(member: Member | None) -> TestClient:
    client = TestClient(app)

    if member is not None:
        settings = get_settings()
        client.cookies.set(settings.session_cookie_name, member.session_token)
        client.cookies.set(csrf_cookie_name(settings), member.csrf_token)

    return client


def headers_for(member: Member | None) -> dict[str, str]:
    headers = {"Origin": TRUSTED_ORIGIN}

    if member is not None:
        headers["X-CSRF-Token"] = member.csrf_token

    return headers


def claim(member: Member | None, body: dict[str, Any] | None = None):
    with client_for(member) as client:
        return client.post(CLAIM, headers=headers_for(member), json=body)


def read_target(member: Member | None):
    with client_for(member) as client:
        return client.get(TARGET)


def stage_of(workspace_id: int) -> OnboardingStage | None:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)

        return None if workspace is None else workspace.onboarding_stage


def claims_for(workspace_id: int) -> list[ProviderLeagueClaim]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(ProviderLeagueClaim).where(ProviderLeagueClaim.workspace_id == workspace_id)
            )
        )
        session.expunge_all()

        return rows


def claims_for_league(league_id: str) -> int:
    with SessionFactory() as session:
        return int(
            session.scalar(
                select(func.count()).where(
                    ProviderLeagueClaim.provider == FantasyProvider.ESPN,
                    ProviderLeagueClaim.normalized_provider_league_id == league_id,
                )
            )
            or 0
        )


def data_counts() -> tuple[int, ...]:
    with SessionFactory() as session:
        return tuple(
            int(session.scalar(select(func.count()).select_from(model)) or 0)
            for model in (League, Season, BackgroundJob)
        )


def residual_rows(seeded: Seeded) -> dict[str, int]:
    """Every row the losing registration could have left behind."""

    user_ids = (seeded.administrator.user_id, seeded.spectator.user_id)

    with SessionFactory() as session:

        def count(model: Any, *criteria: Any) -> int:
            return int(session.scalar(select(func.count()).select_from(model).where(*criteria)))

        return {
            "workspace": count(Installation, Installation.id == seeded.workspace_id),
            "users": count(User, User.id.in_(user_ids)),
            "sessions": count(AuthSession, AuthSession.user_id.in_(user_ids)),
            "verification_tokens": count(
                EmailVerificationToken, EmailVerificationToken.user_id.in_(user_ids)
            ),
            "reset_tokens": count(PasswordResetToken, PasswordResetToken.user_id.in_(user_ids)),
            "connections": count(
                ProviderConnection, ProviderConnection.installation_id == seeded.workspace_id
            ),
            "claims": count(
                ProviderLeagueClaim, ProviderLeagueClaim.workspace_id == seeded.workspace_id
            ),
            "leagues": count(League, League.installation_id == seeded.workspace_id),
            "jobs": count(BackgroundJob, BackgroundJob.workspace_id == seeded.workspace_id),
        }


NOTHING_LEFT = dict.fromkeys(
    (
        "workspace",
        "users",
        "sessions",
        "verification_tokens",
        "reset_tokens",
        "connections",
        "claims",
        "leagues",
        "jobs",
    ),
    0,
)


# --------------------------------------------------------------------------
# Authorization and stage
# --------------------------------------------------------------------------


def test_unauthenticated_requests_are_rejected() -> None:
    assert read_target(None).status_code == status.HTTP_401_UNAUTHORIZED
    assert claim(None).status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("verified", [True, False])
def test_spectators_and_unverified_administrators_are_rejected(
    seed: Callable[..., Seeded], verified: bool
) -> None:
    seeded = seed(verified=verified)
    member = seeded.spectator if verified else seeded.administrator

    for response in (read_target(member), claim(member)):
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["code"] == "provider_claim_forbidden"

    assert claims_for(seeded.workspace_id) == []


@pytest.mark.parametrize(
    ("registration_status", "code"),
    [
        (WorkspaceRegistrationStatus.ACTIVE, "workspace_inactive"),
        (WorkspaceRegistrationStatus.SUSPENDED, "workspace_suspended"),
        (WorkspaceRegistrationStatus.DELETION_PENDING, "workspace_inactive"),
    ],
)
def test_non_provisional_workspaces_are_rejected(
    seed: Callable[..., Seeded], registration_status: WorkspaceRegistrationStatus, code: str
) -> None:
    seeded = seed(
        registration_status=registration_status, installation_status=InstallationStatus.ACTIVE
    )

    response = claim(seeded.administrator)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == code
    assert claims_for(seeded.workspace_id) == []


@pytest.mark.parametrize(
    "stage", sorted(set(OnboardingStage) - {OnboardingStage.LEAGUE_CLAIM_PENDING})
)
def test_only_league_claim_pending_may_read_or_claim(
    seed: Callable[..., Seeded], stage: OnboardingStage
) -> None:
    seeded = seed(onboarding_stage=stage)

    for response in (read_target(seeded.administrator), claim(seeded.administrator)):
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == "onboarding_stage_invalid"

    assert stage_of(seeded.workspace_id) is stage
    assert claims_for(seeded.workspace_id) == []


def test_missing_validated_target_cannot_be_claimed(seed: Callable[..., Seeded]) -> None:
    seeded = seed(access_mode=None)

    for response in (read_target(seeded.administrator), claim(seeded.administrator)):
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == "provider_target_unavailable"

    assert stage_of(seeded.workspace_id) is OnboardingStage.LEAGUE_CLAIM_PENDING


@pytest.mark.parametrize(
    "field_name", ["workspace_id", "user_id", "connection_id", "claim_owner", "league_id"]
)
def test_ownership_and_target_fields_in_the_body_are_rejected(
    seed: Callable[..., Seeded], field_name: str
) -> None:
    seeded = seed()

    response = claim(seeded.administrator, {field_name: 1})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert claims_for(seeded.workspace_id) == []


def test_claim_requires_csrf(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    with client_for(seeded.administrator) as client:
        response = client.post(CLAIM, headers={"Origin": TRUSTED_ORIGIN})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert claims_for(seeded.workspace_id) == []


# --------------------------------------------------------------------------
# Safe target
# --------------------------------------------------------------------------


@pytest.mark.parametrize("access_mode", [ProviderAccessMode.PUBLIC, ProviderAccessMode.PRIVATE])
def test_target_response_is_safe(
    seed: Callable[..., Seeded], access_mode: ProviderAccessMode
) -> None:
    seeded = seed(league_id="424242", access_mode=access_mode)

    response = read_target(seeded.administrator)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "provider": "espn",
        "league_id": "424242",
        "season_year": 2024,
        "access_mode": access_mode.value,
    }

    with SessionFactory() as session:
        connection = session.scalar(
            select(ProviderConnection).where(
                ProviderConnection.installation_id == seeded.workspace_id
            )
        )
        assert connection is not None
        ciphertext = connection.encrypted_credentials

    for forbidden in (
        SYNTHETIC_SWID,
        SYNTHETIC_S2,
        "credential",
        "encrypted",
        "version",
        "username",
        "internal-",
        "swid",
        "espn_s2",
    ):
        assert forbidden not in response.text

    if ciphertext is not None:
        assert ciphertext not in response.text


# --------------------------------------------------------------------------
# Successful claim
# --------------------------------------------------------------------------


def test_successful_claim_is_atomic_and_creates_no_data(seed: Callable[..., Seeded]) -> None:
    seeded = seed(league_id="0515151", access_mode=ProviderAccessMode.PRIVATE)
    counts_before = data_counts()

    response = claim(seeded.administrator, {})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "claimed",
        "code": "provider_league_claimed",
        "onboarding_stage": "initial_sync_pending",
        "target": {
            "provider": "espn",
            "league_id": "0515151",
            "season_year": 2024,
            "access_mode": "private",
        },
    }
    assert SYNTHETIC_S2 not in response.text

    [stored] = claims_for(seeded.workspace_id)
    assert stored.provider is FantasyProvider.ESPN
    assert stored.normalized_provider_league_id == "0515151"
    assert stored.league_id is None

    with SessionFactory() as session:
        workspace = session.get(Installation, seeded.workspace_id)
        assert workspace is not None
        assert workspace.onboarding_stage is OnboardingStage.INITIAL_SYNC_PENDING
        assert workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
        assert workspace.status is InstallationStatus.SETUP_IN_PROGRESS

    assert data_counts() == counts_before


def test_claim_with_empty_body_is_accepted(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    assert claim(seeded.administrator).status_code == status.HTTP_200_OK


def test_repeated_submission_cannot_create_another_claim(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    assert claim(seeded.administrator).status_code == status.HTTP_200_OK

    repeat = claim(seeded.administrator)

    assert repeat.status_code == status.HTTP_409_CONFLICT
    assert repeat.json()["code"] == "onboarding_stage_invalid"
    assert len(claims_for(seeded.workspace_id)) == 1
    assert stage_of(seeded.workspace_id) is OnboardingStage.INITIAL_SYNC_PENDING


def run_concurrently(*calls: Callable[[], Any]) -> list[Any]:
    results: list[Any] = [None] * len(calls)

    def worker(index: int, call: Callable[[], Any]) -> None:
        results[index] = call()

    threads = [threading.Thread(target=worker, args=(i, c)) for i, c in enumerate(calls)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=30)

    return results


def test_concurrent_repeated_submission_from_one_workspace(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    responses = run_concurrently(
        lambda: claim(seeded.administrator), lambda: claim(seeded.administrator)
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(claims_for(seeded.workspace_id)) == 1
    assert stage_of(seeded.workspace_id) is OnboardingStage.INITIAL_SYNC_PENDING


# --------------------------------------------------------------------------
# Duplicate claim
# --------------------------------------------------------------------------


def assert_discloses_nothing(text_body: str, winner: Seeded) -> None:
    for value in (winner.slug, winner.admin_email, str(winner.workspace_id), "Claim Administrator"):
        assert value not in text_body


def test_duplicate_claim_returns_safe_conflict_and_removes_loser(
    seed: Callable[..., Seeded],
) -> None:
    winner = seed(league_id="777777")
    loser = seed(league_id="777777", access_mode=ProviderAccessMode.PRIVATE, with_tokens=True)
    assert residual_rows(loser)["verification_tokens"] == 1
    assert residual_rows(loser)["reset_tokens"] == 1
    counts_before = data_counts()

    assert claim(winner.administrator).status_code == status.HTTP_200_OK

    response = claim(loser.administrator)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == CONFLICT
    assert_discloses_nothing(response.text, winner)

    cleared = " ".join(response.headers.get_list("set-cookie"))
    settings = get_settings()
    assert f"{settings.session_cookie_name}=" in cleared
    assert f"{csrf_cookie_name(settings)}=" in cleared
    assert "Max-Age=0" in cleared or "expires=" in cleared.lower()

    assert residual_rows(loser) == NOTHING_LEFT
    assert claims_for_league("777777") == 1
    assert len(claims_for(winner.workspace_id)) == 1
    assert stage_of(winner.workspace_id) is OnboardingStage.INITIAL_SYNC_PENDING
    assert data_counts() == counts_before

    # The removed session is simply unauthenticated, never session_replaced.
    for member in (loser.administrator, loser.spectator):
        with client_for(member) as client:
            me = client.get("/api/auth/me")

        assert me.status_code == status.HTTP_401_UNAUTHORIZED
        assert me.json() == {"detail": "Authentication is required."}


def test_concurrent_claims_from_two_workspaces_have_one_winner(
    seed: Callable[..., Seeded],
) -> None:
    first = seed(league_id="888888")
    second = seed(league_id="888888")
    counts_before = data_counts()

    # A separate PostgreSQL connection blocks claim inserts until both
    # claim transactions have locked their workspaces and are waiting.
    blocker = engine.connect()
    blocker_transaction = blocker.begin()
    blocker.execute(text("LOCK TABLE provider_league_claims IN SHARE ROW EXCLUSIVE MODE"))

    results: list[Any] = [None, None]
    threads = [
        threading.Thread(
            target=lambda index=index, member=member: results.__setitem__(index, claim(member))
        )
        for index, member in enumerate((first.administrator, second.administrator))
    ]

    try:
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + 15
        waiting = 0

        while time.monotonic() < deadline:
            with engine.connect() as probe:
                waiting = int(
                    probe.scalar(
                        text(
                            "SELECT count(*) FROM pg_locks "
                            "WHERE relation = 'provider_league_claims'::regclass "
                            "AND NOT granted"
                        )
                    )
                    or 0
                )

            if waiting >= 2:
                break

            time.sleep(0.05)

        assert waiting >= 2, "Both claim transactions should be waiting concurrently."
    finally:
        blocker_transaction.commit()
        blocker.close()

    for thread in threads:
        thread.join(timeout=30)

    statuses = sorted(response.status_code for response in results)
    assert statuses == [200, 409]

    winner, loser = (first, second) if results[0].status_code == 200 else (second, first)
    losing_response = results[1] if winner is first else results[0]

    assert losing_response.json() == CONFLICT
    assert_discloses_nothing(losing_response.text, winner)
    assert claims_for_league("888888") == 1
    assert stage_of(winner.workspace_id) is OnboardingStage.INITIAL_SYNC_PENDING
    assert residual_rows(loser) == NOTHING_LEFT
    assert data_counts() == counts_before


def test_season_is_not_part_of_claim_identity(seed: Callable[..., Seeded]) -> None:
    winner = seed(league_id="999999")
    loser = seed(league_id="999999")

    with SessionFactory() as session:
        connection = session.scalar(
            select(ProviderConnection).where(
                ProviderConnection.installation_id == loser.workspace_id
            )
        )
        assert connection is not None
        connection.provider_season_year = 2019
        session.commit()

    assert claim(winner.administrator).status_code == status.HTTP_200_OK
    assert claim(loser.administrator).json() == CONFLICT
    assert residual_rows(loser) == NOTHING_LEFT
