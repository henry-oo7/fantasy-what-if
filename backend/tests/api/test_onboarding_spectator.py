"""GET/POST /api/onboarding/spectator: initial shared spectator setup."""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.api.auth_dependencies import get_current_user
from app.auth.cookies import csrf_cookie_name
from app.auth.security import verify_password
from app.core.settings import get_settings
from app.db.auth_models import User, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository

TRUSTED_ORIGIN = "http://localhost:5173"
ENDPOINT = "/api/onboarding/spectator"
PASSWORD = "shared league phrase 00001"
OTHER_PASSWORD = "different league phrase 002"


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


@dataclass(frozen=True)
class Seeded:
    workspace_id: int
    slug: str
    administrator: Member


@pytest.fixture
def seed() -> Iterator[Callable[..., Seeded]]:
    created: list[int] = []

    def create(
        *,
        stage: OnboardingStage = OnboardingStage.SPECTATOR_SETUP_PENDING,
        registration_status: WorkspaceRegistrationStatus = WorkspaceRegistrationStatus.PROVISIONAL,
        installation_status: InstallationStatus = InstallationStatus.SETUP_IN_PROGRESS,
        verified: bool = True,
    ) -> Seeded:
        suffix = uuid4().hex[:12]
        slug = f"onboard-spec-{suffix}"

        with SessionFactory() as session:
            workspace = Installation(
                application_name="Spectator Onboarding League",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                onboarding_stage=stage,
            )
            session.add(workspace)
            session.flush()
            created.append(workspace.id)

            administrator = AuthenticationRepository(session).create_user(
                username=f"internal-admin-{suffix}",
                display_name="Onboarding Administrator",
                email=f"onboard-spec-{suffix}@example.com",
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC) if verified else None
            admin_session = SessionRepository(session).create_session(
                user=administrator, lifetime_hours=1
            )
            session.commit()

            return Seeded(
                workspace_id=workspace.id,
                slug=slug,
                administrator=Member(admin_session.session_token, admin_session.csrf_token),
            )

    yield create

    with SessionFactory() as session:
        for workspace_id in created:
            session.execute(text("DELETE FROM installations WHERE id = :id"), {"id": workspace_id})

        session.commit()


def call(method: str, member: Member | None, body: dict[str, Any] | None = None):
    with TestClient(app) as client:
        headers = {"Origin": TRUSTED_ORIGIN}

        if member is not None:
            settings = get_settings()
            client.cookies.set(settings.session_cookie_name, member.session_token)
            client.cookies.set(csrf_cookie_name(settings), member.csrf_token)
            headers["X-CSRF-Token"] = member.csrf_token

        return client.request(method, ENDPOINT, headers=headers, json=body)


def spectators(workspace_id: int) -> list[User]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(User).where(
                    User.workspace_id == workspace_id, User.role == UserRole.SPECTATOR
                )
            )
        )
        session.expunge_all()

        return rows


def workspace_state(workspace_id: int) -> tuple[Any, ...]:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)
        assert workspace is not None

        return (
            workspace.onboarding_stage,
            workspace.registration_status,
            workspace.status,
            workspace.active_league_id,
        )


def test_unauthenticated_requests_are_rejected() -> None:
    assert call("GET", None).status_code == status.HTTP_401_UNAUTHORIZED
    assert call("POST", None, {"password": PASSWORD}).status_code == 401


def test_unverified_administrator_is_rejected(seed: Callable[..., Seeded]) -> None:
    seeded = seed(verified=False)

    response = call("POST", seeded.administrator, {"password": PASSWORD})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "spectator_setup_forbidden"
    assert spectators(seeded.workspace_id) == []


@pytest.mark.parametrize(
    "stage",
    sorted(
        set(OnboardingStage) - {OnboardingStage.SPECTATOR_SETUP_PENDING, OnboardingStage.COMPLETE}
    ),
)
def test_only_the_spectator_setup_stage_is_accepted(
    seed: Callable[..., Seeded], stage: OnboardingStage
) -> None:
    seeded = seed(stage=stage)

    for response in (
        call("GET", seeded.administrator),
        call("POST", seeded.administrator, {"password": PASSWORD}),
    ):
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == "onboarding_stage_invalid"

    assert spectators(seeded.workspace_id) == []


@pytest.mark.parametrize(
    ("registration_status", "code"),
    [
        (WorkspaceRegistrationStatus.ACTIVE, "workspace_inactive"),
        (WorkspaceRegistrationStatus.SUSPENDED, "workspace_suspended"),
    ],
)
def test_non_provisional_workspaces_are_rejected(
    seed: Callable[..., Seeded], registration_status: WorkspaceRegistrationStatus, code: str
) -> None:
    seeded = seed(
        registration_status=registration_status, installation_status=InstallationStatus.ACTIVE
    )

    response = call("POST", seeded.administrator, {"password": PASSWORD})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == code


@pytest.mark.parametrize(
    "body",
    [
        {"password": "x" * 13},
        {"password": "x" * 129},
        {"password": PASSWORD, "username": "chosen-name"},
        {"password": PASSWORD, "email": "viewer@example.com"},
        {"password": PASSWORD, "workspace_id": 1},
        {"password": PASSWORD, "confirmation": PASSWORD},
    ],
)
def test_invalid_bodies_are_rejected_locally(
    seed: Callable[..., Seeded], body: dict[str, Any]
) -> None:
    seeded = seed()

    response = call("POST", seeded.administrator, body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert body["password"] not in response.text
    assert spectators(seeded.workspace_id) == []


def test_read_is_safe(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    response = call("GET", seeded.administrator)

    assert response.json() == {
        "workspace_slug": seeded.slug,
        "configured": False,
        "enabled": False,
        "onboarding_stage": "spectator_setup_pending",
    }


def test_setup_creates_one_hashed_spectator_and_completes_without_activation(
    seed: Callable[..., Seeded],
) -> None:
    seeded = seed()

    response = call("POST", seeded.administrator, {"password": PASSWORD})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "workspace_slug": seeded.slug,
        "configured": True,
        "enabled": True,
        "onboarding_stage": "complete",
    }

    [spectator] = spectators(seeded.workspace_id)
    for forbidden in (PASSWORD, spectator.username, spectator.password_hash, "username", "email"):
        assert forbidden not in response.text

    assert spectator.username.startswith("spectator-")
    assert spectator.email is None
    assert spectator.is_active is True
    assert verify_password(PASSWORD, spectator.password_hash)
    assert PASSWORD not in spectator.password_hash

    assert workspace_state(seeded.workspace_id) == (
        OnboardingStage.COMPLETE,
        WorkspaceRegistrationStatus.PROVISIONAL,
        InstallationStatus.SETUP_IN_PROGRESS,
        None,
    )

    # /me keeps the administrator in provisional onboarding: not activated.
    with TestClient(app) as client:
        client.cookies.set(get_settings().session_cookie_name, seeded.administrator.session_token)
        me = client.get("/api/auth/me").json()

    assert me["workspace"]["workspace_access"] == "provisional"
    assert me["workspace"]["onboarding_stage"] == "complete"


def test_repeated_setup_never_replaces_the_password(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    assert call("POST", seeded.administrator, {"password": PASSWORD}).status_code == 200

    repeat = call("POST", seeded.administrator, {"password": OTHER_PASSWORD})

    assert repeat.status_code == status.HTTP_200_OK
    assert repeat.json()["onboarding_stage"] == "complete"
    assert repeat.json()["configured"] is True

    [spectator] = spectators(seeded.workspace_id)
    assert verify_password(PASSWORD, spectator.password_hash)
    assert not verify_password(OTHER_PASSWORD, spectator.password_hash)


def test_concurrent_setup_creates_one_spectator(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    barrier = threading.Barrier(2, timeout=10)
    results: list[Any] = [None, None]

    def worker(index: int, password: str) -> None:
        barrier.wait()
        results[index] = call("POST", seeded.administrator, {"password": password})

    threads = [
        threading.Thread(target=worker, args=(0, PASSWORD)),
        threading.Thread(target=worker, args=(1, OTHER_PASSWORD)),
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=30)

    assert [response.status_code for response in results] == [200, 200]
    [spectator] = spectators(seeded.workspace_id)
    # Exactly one password won; the other request returned the configured state.
    assert verify_password(PASSWORD, spectator.password_hash) != verify_password(
        OTHER_PASSWORD, spectator.password_hash
    )

    with SessionFactory() as session:
        total = session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.workspace_id == seeded.workspace_id, User.role == UserRole.SPECTATOR)
        )

    assert total == 1
    assert workspace_state(seeded.workspace_id)[0] is OnboardingStage.COMPLETE


def test_setup_is_isolated_per_workspace(seed: Callable[..., Seeded]) -> None:
    mine = seed()
    other = seed()

    call("POST", mine.administrator, {"password": PASSWORD})

    assert spectators(other.workspace_id) == []
    assert workspace_state(other.workspace_id)[0] is OnboardingStage.SPECTATOR_SETUP_PENDING
