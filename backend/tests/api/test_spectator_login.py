"""API coverage for workspace-scoped shared spectator login."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.auth_dependencies import get_current_user
from app.auth.security import hash_password
from app.db.auth_models import (
    AuthSession,
    LoginAttempt,
    User,
    UserRole,
)
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import AuthenticationRepository

TRUSTED_ORIGIN = "http://localhost:5173"
SPECTATOR_PASSWORD = "shared spectator passphrase"
ADMIN_PASSWORD = "correct horse battery staple"
SAFE_FAILURE = {
    "detail": "The workspace or spectator credentials are incorrect.",
}


@dataclass(frozen=True, slots=True)
class SeededWorkspace:
    workspace_id: int
    slug: str
    spectator_id: int
    administrator_id: int
    administrator_email: str


@pytest.fixture(autouse=True)
def isolate_login_attempt_rows() -> Iterator[None]:
    """Remove throttle rows created by each spectator-login test."""

    with SessionFactory() as session:
        existing_ids = set(session.scalars(select(LoginAttempt.id)).all())

    try:
        yield
    finally:
        with SessionFactory() as session:
            cleanup = delete(LoginAttempt)

            if existing_ids:
                cleanup = cleanup.where(LoginAttempt.id.not_in(existing_ids))

            session.execute(cleanup)
            session.commit()


@pytest.fixture(autouse=True)
def real_authentication() -> Iterator[None]:
    inherited_override = app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    try:
        yield
    finally:
        if inherited_override is not None:
            app.dependency_overrides[get_current_user] = inherited_override


@pytest.fixture
def seed_workspace() -> Iterator[Callable[..., SeededWorkspace]]:
    created_workspace_ids: list[int] = []

    def create(
        *,
        installation_status: InstallationStatus = InstallationStatus.ACTIVE,
        registration_status: WorkspaceRegistrationStatus | None = (
            WorkspaceRegistrationStatus.ACTIVE
        ),
        onboarding_stage: OnboardingStage | None = OnboardingStage.COMPLETE,
        spectator_active: bool = True,
    ) -> SeededWorkspace:
        suffix = uuid4().hex[:12]
        slug = f"spectator-{suffix}"
        email = f"spectator-admin-{suffix}@example.com"

        with SessionFactory() as session:
            workspace = Installation(
                application_name="Spectator Login League",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                onboarding_stage=onboarding_stage,
            )
            session.add(workspace)
            session.flush()

            repository = AuthenticationRepository(session)
            spectator = repository.create_user(
                username=f"spectator-{suffix}",
                display_name="League Spectator",
                password=SPECTATOR_PASSWORD,
                role=UserRole.SPECTATOR,
                is_active=spectator_active,
                workspace_id=workspace.id,
            )
            administrator = repository.create_user(
                username=f"internal-admin-{suffix}",
                display_name="League Administrator",
                email=email,
                password=ADMIN_PASSWORD,
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC)
            session.commit()

            created_workspace_ids.append(workspace.id)

            return SeededWorkspace(
                workspace_id=workspace.id,
                slug=slug,
                spectator_id=spectator.id,
                administrator_id=administrator.id,
                administrator_email=email,
            )

    yield create

    with SessionFactory() as session:
        for workspace_id in created_workspace_ids:
            users = session.scalars(select(User).where(User.workspace_id == workspace_id)).all()

            for user in users:
                session.delete(user)

            session.flush()

            workspace = session.get(Installation, workspace_id)

            if workspace is not None:
                session.delete(workspace)

        session.commit()


def issue_csrf(client: TestClient) -> str:
    response = client.get("/api/auth/csrf")

    assert response.status_code == status.HTTP_200_OK

    return response.json()["csrf_token"]


def spectator_login(
    client: TestClient,
    *,
    workspace_slug: str,
    password: str = SPECTATOR_PASSWORD,
):
    return client.post(
        "/api/auth/spectator/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": issue_csrf(client),
        },
        json={
            "workspace_slug": workspace_slug,
            "password": password,
        },
    )


def administrator_login(
    client: TestClient,
    *,
    email: str,
):
    return client.post(
        "/api/auth/admin/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": issue_csrf(client),
        },
        json={
            "email": email,
            "password": ADMIN_PASSWORD,
        },
    )


def active_sessions(user_id: int) -> list[AuthSession]:
    with SessionFactory() as session:
        return list(
            session.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user_id,
                    AuthSession.revoked_at.is_(None),
                )
            ).all()
        )


def test_successful_spectator_login_returns_workspace_identity(
    seed_workspace: Callable[..., SeededWorkspace],
) -> None:
    seeded = seed_workspace()

    with TestClient(app) as client:
        response = spectator_login(
            client,
            workspace_slug=seeded.slug.upper(),
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["user"]["id"] == seeded.spectator_id
    assert body["user"]["workspace_id"] == seeded.workspace_id
    assert body["user"]["workspace_slug"] == seeded.slug
    assert body["user"]["role"] == "spectator"
    assert "username" not in body["user"]
    assert body["csrf_token"]


def test_two_spectator_logins_remain_active(
    seed_workspace: Callable[..., SeededWorkspace],
) -> None:
    seeded = seed_workspace()

    with TestClient(app) as first_browser, TestClient(app) as second_browser:
        first = spectator_login(first_browser, workspace_slug=seeded.slug)
        second = spectator_login(second_browser, workspace_slug=seeded.slug)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert len(active_sessions(seeded.spectator_id)) == 2

        assert first_browser.get("/api/auth/me").status_code == status.HTTP_200_OK
        assert second_browser.get("/api/auth/me").status_code == status.HTTP_200_OK


def test_workspace_a_password_fails_against_workspace_b_slug(
    seed_workspace: Callable[..., SeededWorkspace],
) -> None:
    workspace_a = seed_workspace()
    workspace_b = seed_workspace()

    # Give Workspace B a distinct spectator password so Workspace A's
    # password is valid only within Workspace A.
    with SessionFactory() as session:
        spectator_b = session.get(User, workspace_b.spectator_id)
        assert spectator_b is not None
        spectator_b.password_hash = hash_password("workspace b only passphrase")
        session.commit()

    with TestClient(app) as client:
        own_workspace = spectator_login(client, workspace_slug=workspace_a.slug)
        assert own_workspace.status_code == status.HTTP_200_OK

    with TestClient(app) as client:
        response = spectator_login(
            client,
            workspace_slug=workspace_b.slug,
            password=SPECTATOR_PASSWORD,
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == SAFE_FAILURE
    assert active_sessions(workspace_b.spectator_id) == []


@pytest.mark.parametrize(
    "case",
    [
        "unknown_slug",
        "wrong_password",
        "disabled_spectator",
        "suspended_workspace",
        "provisional_workspace",
        "invalid_slug",
    ],
)
def test_spectator_failures_share_one_safe_response(
    seed_workspace: Callable[..., SeededWorkspace],
    case: str,
) -> None:
    slug_override: str | None = None
    password = SPECTATOR_PASSWORD

    if case == "disabled_spectator":
        seeded = seed_workspace(spectator_active=False)
    elif case == "suspended_workspace":
        seeded = seed_workspace(
            registration_status=WorkspaceRegistrationStatus.SUSPENDED,
        )
    elif case == "provisional_workspace":
        seeded = seed_workspace(
            installation_status=InstallationStatus.SETUP_IN_PROGRESS,
            registration_status=WorkspaceRegistrationStatus.PROVISIONAL,
            onboarding_stage=OnboardingStage.SPECTATOR_SETUP_PENDING,
        )
    else:
        seeded = seed_workspace()

    if case == "unknown_slug":
        slug_override = f"missing-{uuid4().hex[:12]}"
    elif case == "wrong_password":
        password = "not the spectator passphrase"
    elif case == "invalid_slug":
        slug_override = "Not A Valid Slug!"

    with TestClient(app) as client:
        response = spectator_login(
            client,
            workspace_slug=slug_override or seeded.slug,
            password=password,
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == SAFE_FAILURE
    assert active_sessions(seeded.spectator_id) == []


def test_spectator_receives_403_on_administrator_routes(
    seed_workspace: Callable[..., SeededWorkspace],
) -> None:
    seeded = seed_workspace()

    with TestClient(app) as client:
        assert spectator_login(client, workspace_slug=seeded.slug).status_code == (
            status.HTTP_200_OK
        )

        response = client.get("/api/admin/sync-status")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Administrator access is required."


def test_repeated_spectator_failures_are_throttled(
    seed_workspace: Callable[..., SeededWorkspace],
) -> None:
    seeded = seed_workspace()
    throttled = None

    with TestClient(app) as client:
        for _ in range(12):
            response = spectator_login(
                client,
                workspace_slug=seeded.slug,
                password="not the spectator passphrase",
            )

            if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                throttled = response
                break

            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        assert throttled is not None
        assert throttled.json() == SAFE_FAILURE
        assert int(throttled.headers["Retry-After"]) > 0

        # The correct password is still refused while throttled.
        blocked = spectator_login(client, workspace_slug=seeded.slug)

    assert blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert active_sessions(seeded.spectator_id) == []


def test_spectator_login_does_not_revoke_administrator_session(
    seed_workspace: Callable[..., SeededWorkspace],
) -> None:
    seeded = seed_workspace()

    with TestClient(app) as admin_browser, TestClient(app) as spectator_browser:
        admin = administrator_login(
            admin_browser,
            email=seeded.administrator_email,
        )
        assert admin.status_code == status.HTTP_200_OK

        spectator = spectator_login(spectator_browser, workspace_slug=seeded.slug)
        assert spectator.status_code == status.HTTP_200_OK

        assert len(active_sessions(seeded.administrator_id)) == 1
        assert admin_browser.get("/api/auth/me").status_code == status.HTTP_200_OK


def test_auth_me_works_for_active_spectator(
    seed_workspace: Callable[..., SeededWorkspace],
) -> None:
    seeded = seed_workspace()

    with TestClient(app) as client:
        assert spectator_login(client, workspace_slug=seeded.slug).status_code == (
            status.HTTP_200_OK
        )

        response = client.get("/api/auth/me")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert "id" not in body and "role" not in body
    assert body["authenticated"] is True
    assert body["navigation_state"] == "legacy_workspace"
    assert body["legacy_workspace"]["role"] == "spectator"
    assert body["legacy_workspace"]["workspace"]["workspace_id"] == seeded.workspace_id


def test_authenticated_spectator_loses_access_after_workspace_suspension(
    seed_workspace: Callable[..., SeededWorkspace],
) -> None:
    seeded = seed_workspace()

    with TestClient(app) as client:
        assert spectator_login(client, workspace_slug=seeded.slug).status_code == (
            status.HTTP_200_OK
        )

        before = client.get("/api/leagues/current")
        assert before.status_code not in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

        with SessionFactory() as session:
            workspace = session.get(Installation, seeded.workspace_id)
            assert workspace is not None
            workspace.registration_status = WorkspaceRegistrationStatus.SUSPENDED
            session.commit()

        after = client.get("/api/leagues/current")

    assert after.status_code == status.HTTP_403_FORBIDDEN
    assert after.json() == {
        "detail": "Workspace access is unavailable.",
        "code": "workspace_suspended",
        "retryable": False,
    }
