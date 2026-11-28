"""GET /api/onboarding/activation and POST /api/onboarding/activate.

Activation must recheck persisted invariants, change nothing on failure,
and never contact a provider, decrypt credentials, enqueue jobs or sync.
"""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from app.db.auth_models import User, UserRole
from app.db.models import League, Matchup, Season, SyncRun, Team
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    FantasySport,
    InstallationStatus,
    OnboardingStage,
    ProviderAccessMode,
    SeasonStatus,
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
from app.providers.credential_vault import ProviderCredentialVault
from app.providers.domain import ProviderCredentials
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.session_repository import SessionRepository
from app.services.spectator_account import create_workspace_spectator, lock_workspace

TRUSTED_ORIGIN = "http://localhost:5173"
# An ACTIVE season must be the current year; startup reconciliation
# completes active seasons from earlier years, as a real import would.
SEASON = datetime.now(UTC).year
SPECTATOR_PASSWORD = "shared league phrase 00001"
KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def real_authentication() -> Iterator[None]:
    inherited = app.dependency_overrides.pop(get_current_user, None)

    try:
        yield
    finally:
        if inherited is not None:
            app.dependency_overrides[get_current_user] = inherited


@pytest.fixture(autouse=True)
def forbid_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail on any network, decryption, enqueue or synchronization attempt."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Activation must not perform this operation.")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)
    monkeypatch.setattr(ProviderCredentialVault, "decrypt", refuse)
    monkeypatch.setattr(BackgroundJobRepository, "enqueue", refuse)
    monkeypatch.setattr("app.services.provider_sync.synchronize_season", refuse)


@dataclass(frozen=True)
class Member:
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class Seeded:
    workspace_id: int
    slug: str
    league_id: str
    administrator: Member
    spectator_username: str | None
    internal_league_id: int | None
    internal_season_id: int | None


@pytest.fixture
def seed() -> Iterator[Callable[..., Seeded]]:
    created: list[int] = []

    def create(
        *,
        stage: OnboardingStage = OnboardingStage.COMPLETE,
        registration_status: WorkspaceRegistrationStatus = WorkspaceRegistrationStatus.PROVISIONAL,
        installation_status: InstallationStatus = InstallationStatus.SETUP_IN_PROGRESS,
        access_mode: ProviderAccessMode = ProviderAccessMode.PUBLIC,
        with_connection: bool = True,
        with_claim: bool = True,
        attach_claim: bool = True,
        league_owner: int | None = None,
        season_year: int = SEASON,
        season_status: SeasonStatus = SeasonStatus.ACTIVE,
        job_status: BackgroundJobStatus | None = BackgroundJobStatus.SUCCEEDED,
        team_count: int = 2,
        matchup_count: int = 1,
        spectator: str = "enabled",
        verified: bool = True,
    ) -> Seeded:
        suffix = uuid4().hex[:12]
        slug = f"activate-{suffix}"
        league_id = str(200000 + uuid4().int % 700000)

        with SessionFactory() as session:
            is_pending = registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
            workspace = Installation(
                application_name="Activation League",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                deletion_requested_at=datetime.now(UTC) if is_pending else None,
                deletion_scheduled_for=(
                    datetime.now(UTC) + timedelta(days=7) if is_pending else None
                ),
                onboarding_stage=stage,
            )
            session.add(workspace)
            session.flush()
            created.append(workspace.id)
            connection = None

            if with_connection:
                encrypted = None
                key_version = None

                if access_mode is ProviderAccessMode.PRIVATE:
                    key_version = 1
                    encrypted = ProviderCredentialVault(keys={1: KEY}).encrypt(
                        provider=FantasyProvider.ESPN,
                        credentials=ProviderCredentials(
                            values={"swid": "{SYNTHETIC-SWID}", "espn_s2": "synthetic-s2-value"}
                        ),
                        key_version=1,
                    )

                connection = ProviderConnection(
                    installation_id=workspace.id,
                    provider=FantasyProvider.ESPN,
                    display_name="ESPN",
                    is_active=True,
                    access_mode=access_mode,
                    provider_league_id=league_id,
                    provider_season_year=SEASON,
                    encrypted_credentials=encrypted,
                    credential_version=key_version,
                    last_validated_at=datetime.now(UTC),
                )
                session.add(connection)
                session.flush()

            league = League(
                installation_id=league_owner or workspace.id,
                provider=FantasyProvider.ESPN,
                provider_league_id=league_id,
                sport=FantasySport.FOOTBALL,
                name="Synthetic Activation League",
            )
            session.add(league)
            session.flush()

            season = Season(
                league_id=league.id,
                year=season_year,
                current_week=3,
                regular_season_weeks=14,
                status=season_status,
                provider_connection_id=connection.id if connection is not None else None,
                provider_season_id=str(season_year),
            )
            session.add(season)
            session.flush()

            teams = []
            for index in range(team_count):
                team = Team(
                    season_id=season.id,
                    provider_team_id=f"team-{index}",
                    name=f"Synthetic Team {index}",
                    wins=0,
                    losses=0,
                    ties=0,
                    points_for=Decimal("0"),
                    points_against=Decimal("0"),
                )
                session.add(team)
                teams.append(team)
            session.flush()

            for week in range(1, matchup_count + 1):
                session.add(
                    Matchup(
                        season_id=season.id,
                        week=week,
                        home_team_id=teams[0].id,
                        away_team_id=teams[1].id if len(teams) > 1 else None,
                    )
                )

            if with_claim:
                session.add(
                    ProviderLeagueClaim(
                        workspace_id=workspace.id,
                        provider=FantasyProvider.ESPN,
                        provider_league_id=league_id,
                        normalized_provider_league_id=league_id,
                        league_id=league.id if attach_claim else None,
                    )
                )

            if job_status is not None:
                now = datetime.now(UTC)
                job = BackgroundJob(
                    workspace_id=workspace.id,
                    job_type=BackgroundJobType.INITIAL_WORKSPACE_SYNC,
                    status=job_status,
                    payload={"trigger": "onboarding"},
                    attempt_count=1,
                    maximum_attempts=3,
                    available_at=now,
                )
                if job_status is BackgroundJobStatus.SUCCEEDED:
                    job.completed_at = now
                elif job_status is BackgroundJobStatus.FAILED:
                    job.failed_at = now
                elif job_status is BackgroundJobStatus.RUNNING:
                    job.claimed_at = now
                    job.started_at = now
                    job.claimed_by = "synthetic-worker"
                    job.lease_expires_at = now
                session.add(job)

            administrator = AuthenticationRepository(session).create_user(
                username=f"internal-admin-{suffix}",
                display_name="Activation Administrator",
                email=f"activate-{suffix}@example.com",
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC) if verified else None
            spectator_username = None

            if spectator in ("enabled", "disabled", "with_email"):
                lock_workspace(session, workspace.id)
                created_spectator = create_workspace_spectator(
                    session, workspace_id=workspace.id, password=SPECTATOR_PASSWORD
                )
                created_spectator.is_active = spectator != "disabled"
                if spectator == "with_email":
                    created_spectator.email = f"viewer-{suffix}@example.com"
                spectator_username = created_spectator.username

            admin_session = SessionRepository(session).create_session(
                user=administrator, lifetime_hours=1
            )
            session.commit()

            return Seeded(
                workspace_id=workspace.id,
                slug=slug,
                league_id=league_id,
                administrator=Member(admin_session.session_token, admin_session.csrf_token),
                spectator_username=spectator_username,
                internal_league_id=league.id,
                internal_season_id=season.id,
            )

    yield create

    with SessionFactory() as session:
        for workspace_id in created:
            session.execute(
                text(
                    "UPDATE installations SET active_league_id = NULL, active_season_id = NULL "
                    "WHERE id = :id"
                ),
                {"id": workspace_id},
            )
            session.execute(
                text("DELETE FROM leagues WHERE installation_id = :id"), {"id": workspace_id}
            )
            session.execute(text("DELETE FROM installations WHERE id = :id"), {"id": workspace_id})

        session.commit()


def call(method: str, path: str, member: Member | None, body: dict[str, Any] | None = None):
    with TestClient(app) as client:
        headers = {"Origin": TRUSTED_ORIGIN}

        if member is not None:
            settings = get_settings()
            client.cookies.set(settings.session_cookie_name, member.session_token)
            client.cookies.set(csrf_cookie_name(settings), member.csrf_token)
            headers["X-CSRF-Token"] = member.csrf_token

        return client.request(method, path, headers=headers, json=body)


def activate(member: Member | None, body: dict[str, Any] | None = None):
    return call("POST", "/api/onboarding/activate", member, body)


def review(member: Member | None):
    return call("GET", "/api/onboarding/activation", member)


def lifecycle(workspace_id: int) -> tuple[Any, ...]:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)
        assert workspace is not None

        return (
            workspace.registration_status,
            workspace.status,
            workspace.onboarding_stage,
            workspace.active_league_id,
            workspace.active_season_id,
            workspace.setup_completed_at,
        )


def row_counts() -> tuple[int, ...]:
    with SessionFactory() as session:
        return tuple(
            int(session.scalar(select(func.count()).select_from(model)) or 0)
            for model in (
                League,
                Season,
                ProviderLeagueClaim,
                ProviderConnection,
                BackgroundJob,
                User,
                Team,
                Matchup,
                SyncRun,
            )
        )


PROVISIONAL_UNCHANGED = (
    WorkspaceRegistrationStatus.PROVISIONAL,
    InstallationStatus.SETUP_IN_PROGRESS,
    OnboardingStage.COMPLETE,
    None,
    None,
    None,
)


# --------------------------------------------------------------------------
# Authorization and stage
# --------------------------------------------------------------------------


def test_unauthenticated_requests_are_rejected() -> None:
    assert activate(None).status_code == status.HTTP_401_UNAUTHORIZED
    assert review(None).status_code == status.HTTP_401_UNAUTHORIZED


def test_unverified_administrator_is_rejected(seed: Callable[..., Seeded]) -> None:
    seeded = seed(verified=False)

    response = activate(seeded.administrator)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "activation_forbidden"
    assert lifecycle(seeded.workspace_id) == PROVISIONAL_UNCHANGED


@pytest.mark.parametrize("stage", sorted(set(OnboardingStage) - {OnboardingStage.COMPLETE}))
def test_only_the_complete_stage_may_activate(
    seed: Callable[..., Seeded], stage: OnboardingStage
) -> None:
    seeded = seed(stage=stage)

    for response in (activate(seeded.administrator), review(seeded.administrator)):
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == "onboarding_stage_invalid"

    assert lifecycle(seeded.workspace_id)[0] is WorkspaceRegistrationStatus.PROVISIONAL


@pytest.mark.parametrize(
    ("registration_status", "code"),
    [
        (WorkspaceRegistrationStatus.SUSPENDED, "workspace_suspended"),
        (WorkspaceRegistrationStatus.DELETION_PENDING, "workspace_inactive"),
        (WorkspaceRegistrationStatus.DELETED, "workspace_inactive"),
    ],
)
def test_unusable_workspaces_are_rejected(
    seed: Callable[..., Seeded], registration_status: WorkspaceRegistrationStatus, code: str
) -> None:
    seeded = seed(registration_status=registration_status)

    response = activate(seeded.administrator)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == code


@pytest.mark.parametrize(
    "field_name",
    ["workspace_id", "league_id", "season_id", "claim_id", "connection_id", "user_id", "status"],
)
def test_body_fields_are_rejected(seed: Callable[..., Seeded], field_name: str) -> None:
    seeded = seed()

    assert activate(seeded.administrator, {field_name: 1}).status_code == 422
    assert lifecycle(seeded.workspace_id) == PROVISIONAL_UNCHANGED


# --------------------------------------------------------------------------
# Invariants: every failure changes nothing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "category"),
    [
        ({"with_connection": False}, "provider_connection"),
        ({"with_claim": False}, "provider_connection"),
        ({"job_status": None}, "synchronization"),
        ({"job_status": BackgroundJobStatus.FAILED}, "synchronization"),
        ({"job_status": BackgroundJobStatus.RUNNING}, "synchronization"),
        ({"job_status": BackgroundJobStatus.PENDING}, "synchronization"),
        ({"attach_claim": False}, "league_data"),
        ({"season_year": SEASON - 1}, "league_data"),
        ({"season_status": SeasonStatus.DRAFT}, "league_data"),
        ({"team_count": 1, "matchup_count": 1}, "league_data"),
        ({"matchup_count": 0}, "league_data"),
        ({"spectator": "none"}, "spectator_access"),
        ({"spectator": "disabled"}, "spectator_access"),
        ({"spectator": "with_email"}, "spectator_access"),
    ],
)
def test_unmet_invariants_block_activation_without_changes(
    seed: Callable[..., Seeded], options: dict[str, Any], category: str
) -> None:
    seeded = seed(**options)
    before = row_counts()

    response = activate(seeded.administrator)

    assert response.status_code == status.HTTP_409_CONFLICT
    body = response.json()
    assert body["code"] == "activation_not_ready"
    assert body["category"] == category
    assert set(body) == {"detail", "code", "category", "retryable"}
    assert lifecycle(seeded.workspace_id) == PROVISIONAL_UNCHANGED
    assert row_counts() == before

    summary = review(seeded.administrator).json()
    assert summary["ready"] is False
    assert summary["blocking_category"] == category


def test_claim_attached_to_another_workspaces_league_is_rejected(
    seed: Callable[..., Seeded],
) -> None:
    other = seed()
    seeded = seed(league_owner=other.workspace_id)

    response = activate(seeded.administrator)

    assert response.json()["category"] == "league_data"
    assert lifecycle(seeded.workspace_id) == PROVISIONAL_UNCHANGED


def test_spectator_from_another_workspace_does_not_count(seed: Callable[..., Seeded]) -> None:
    seed()  # Another workspace with an enabled spectator.
    seeded = seed(spectator="none")

    assert activate(seeded.administrator).json()["category"] == "spectator_access"


def test_conflicting_active_selection_is_rejected(seed: Callable[..., Seeded]) -> None:
    other = seed()
    seeded = seed()

    with SessionFactory() as session:
        workspace = session.get(Installation, seeded.workspace_id)
        assert workspace is not None
        workspace.active_league_id = other.internal_league_id
        session.commit()

    assert activate(seeded.administrator).json()["category"] == "league_data"

    with SessionFactory() as session:
        workspace = session.get(Installation, seeded.workspace_id)
        assert workspace is not None
        assert workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL


# --------------------------------------------------------------------------
# Success, idempotency and concurrency
# --------------------------------------------------------------------------


@pytest.mark.parametrize("access_mode", [ProviderAccessMode.PUBLIC, ProviderAccessMode.PRIVATE])
def test_activation_sets_lifecycle_and_active_selection(
    seed: Callable[..., Seeded], access_mode: ProviderAccessMode
) -> None:
    seeded = seed(access_mode=access_mode)
    before = row_counts()

    summary = review(seeded.administrator)
    assert summary.json() == {
        "workspace_slug": seeded.slug,
        "provider": "espn",
        "league_id": seeded.league_id,
        "season_year": SEASON,
        "access_mode": access_mode.value,
        "synchronization_complete": True,
        "spectator_access_ready": True,
        "ready": True,
        "blocking_category": None,
    }

    response = activate(seeded.administrator, {})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "activated",
        "workspace_slug": seeded.slug,
        "workspace_access": "active",
        "onboarding_stage": "complete",
    }

    registration, installation, stage, league_id, season_id, completed_at = lifecycle(
        seeded.workspace_id
    )
    assert registration is WorkspaceRegistrationStatus.ACTIVE
    assert installation is InstallationStatus.ACTIVE
    assert stage is OnboardingStage.COMPLETE
    assert league_id == seeded.internal_league_id
    assert season_id == seeded.internal_season_id
    assert completed_at is not None
    assert row_counts() == before

    for text_body in (summary.text, response.text):
        assert seeded.spectator_username is not None
        for forbidden in (
            seeded.spectator_username,
            "synthetic-s2-value",
            "SYNTHETIC-SWID",
            "encrypted",
            "credential",
            "payload",
            "hash",
            str(seeded.internal_league_id) + ",",
        ):
            assert forbidden not in text_body

    with TestClient(app) as client:
        client.cookies.set(get_settings().session_cookie_name, seeded.administrator.session_token)
        me = client.get("/api/auth/me").json()

    assert me["legacy_workspace"]["workspace"]["workspace_access"] == "active"


def test_completed_historical_season_leaves_no_active_season(
    seed: Callable[..., Seeded],
) -> None:
    seeded = seed(season_status=SeasonStatus.COMPLETED)

    assert activate(seeded.administrator).status_code == status.HTTP_200_OK

    _, _, _, league_id, season_id, _ = lifecycle(seeded.workspace_id)
    assert league_id == seeded.internal_league_id
    assert season_id is None


def test_repeated_activation_returns_already_active(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    assert activate(seeded.administrator).json()["status"] == "activated"
    first = lifecycle(seeded.workspace_id)

    repeat = activate(seeded.administrator)

    assert repeat.status_code == status.HTTP_200_OK
    assert repeat.json()["status"] == "already_active"
    assert lifecycle(seeded.workspace_id) == first


def test_concurrent_activation_has_one_transition(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    before = row_counts()
    barrier = threading.Barrier(2, timeout=10)
    results: list[Any] = [None, None]

    def worker(index: int) -> None:
        barrier.wait()
        results[index] = activate(seeded.administrator)

    threads = [threading.Thread(target=worker, args=(index,)) for index in (0, 1)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=30)

    assert [response.status_code for response in results] == [200, 200]
    assert sorted(response.json()["status"] for response in results) == [
        "activated",
        "already_active",
    ]
    assert lifecycle(seeded.workspace_id)[0] is WorkspaceRegistrationStatus.ACTIVE
    assert row_counts() == before


def test_activation_is_workspace_isolated(seed: Callable[..., Seeded]) -> None:
    mine = seed()
    other = seed()

    assert activate(mine.administrator).status_code == status.HTTP_200_OK
    assert lifecycle(other.workspace_id) == PROVISIONAL_UNCHANGED
