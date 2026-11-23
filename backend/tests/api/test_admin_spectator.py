"""Administrator management of the workspace's shared spectator (real auth and DB)."""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.api.auth_dependencies import get_current_user
from app.auth.cookies import csrf_cookie_name
from app.core.settings import get_settings
from app.db.auth_models import AuthSession, LoginAttempt, SessionRevocationReason, User, UserRole
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
from app.services.spectator_account import create_workspace_spectator, lock_workspace

TRUSTED_ORIGIN = "http://localhost:5173"
PASSWORD = "original shared phrase 0001"
NEW_PASSWORD = "replacement shared phrase 0002"
GENERIC_SPECTATOR_FAILURE = "The workspace or spectator credentials are incorrect."


@pytest.fixture(autouse=True)
def real_authentication() -> Iterator[None]:
    inherited = app.dependency_overrides.pop(get_current_user, None)

    try:
        yield
    finally:
        if inherited is not None:
            app.dependency_overrides[get_current_user] = inherited


@pytest.fixture(autouse=True)
def isolate_login_attempt_rows() -> Iterator[None]:
    with SessionFactory() as session:
        existing = set(session.scalars(select(LoginAttempt.id)).all())

    yield

    with SessionFactory() as session:
        cleanup = delete(LoginAttempt)

        if existing:
            cleanup = cleanup.where(LoginAttempt.id.not_in(existing))

        session.execute(cleanup)
        session.commit()


@dataclass(frozen=True)
class Member:
    session_token: str
    csrf_token: str
    user_id: int


@dataclass(frozen=True)
class Seeded:
    workspace_id: int
    slug: str
    administrator: Member
    spectator_id: int | None
    spectator_username: str | None


@pytest.fixture
def seed() -> Iterator[Callable[..., Seeded]]:
    created: list[int] = []

    def create(
        *,
        with_spectator: bool = True,
        registration_status: WorkspaceRegistrationStatus = WorkspaceRegistrationStatus.ACTIVE,
        installation_status: InstallationStatus = InstallationStatus.ACTIVE,
    ) -> Seeded:
        suffix = uuid4().hex[:12]
        slug = f"spectate-{suffix}"

        with SessionFactory() as session:
            workspace = Installation(
                application_name="Spectator Admin League",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                onboarding_stage=OnboardingStage.COMPLETE,
            )
            session.add(workspace)
            session.flush()
            created.append(workspace.id)

            administrator = AuthenticationRepository(session).create_user(
                username=f"internal-admin-{suffix}",
                display_name="Spectator Administrator",
                email=f"spectate-{suffix}@example.com",
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC)
            spectator = None

            if with_spectator:
                lock_workspace(session, workspace.id)
                spectator = create_workspace_spectator(
                    session, workspace_id=workspace.id, password=PASSWORD
                )

            admin_session = SessionRepository(session).create_session(
                user=administrator, lifetime_hours=1
            )
            session.commit()

            return Seeded(
                workspace_id=workspace.id,
                slug=slug,
                administrator=Member(
                    admin_session.session_token, admin_session.csrf_token, administrator.id
                ),
                spectator_id=spectator.id if spectator else None,
                spectator_username=spectator.username if spectator else None,
            )

    yield create

    with SessionFactory() as session:
        for workspace_id in created:
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


def spectator_login(slug: str, password: str, client: TestClient | None = None):
    owned = client is None
    active = client or TestClient(app)

    try:
        csrf = active.get("/api/auth/csrf").json()["csrf_token"]

        return active.post(
            "/api/auth/spectator/login",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf},
            json={"workspace_slug": slug, "password": password},
        )
    finally:
        if owned:
            active.close()


def spectator_sessions(user_id: int) -> list[AuthSession]:
    with SessionFactory() as session:
        rows = list(session.scalars(select(AuthSession).where(AuthSession.user_id == user_id)))
        session.expunge_all()

        return rows


ALLOWED_KEYS = {"workspace_slug", "configured", "enabled", "revoked_sessions"}


def assert_no_leak(text_body: str, seeded: Seeded, *passwords: str) -> None:
    assert set(json.loads(text_body)) <= ALLOWED_KEYS

    for forbidden in ("username", "email", "password", "hash", "argon2", *passwords):
        assert forbidden not in text_body

    if seeded.spectator_username is not None:
        assert seeded.spectator_username not in text_body


def test_status_is_safe(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    response = call("GET", "/api/admin/spectator", seeded.administrator)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"workspace_slug": seeded.slug, "configured": True, "enabled": True}
    assert_no_leak(response.text, seeded, PASSWORD)


def test_unconfigured_status(seed: Callable[..., Seeded]) -> None:
    seeded = seed(with_spectator=False)

    assert call("GET", "/api/admin/spectator", seeded.administrator).json() == {
        "workspace_slug": seeded.slug,
        "configured": False,
        "enabled": False,
    }


def test_create_accepts_only_a_password(seed: Callable[..., Seeded]) -> None:
    seeded = seed(with_spectator=False)

    rejected = call(
        "POST",
        "/api/admin/spectator",
        seeded.administrator,
        {"password": PASSWORD, "username": "chosen", "email": "x@example.com"},
    )
    assert rejected.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    created = call("POST", "/api/admin/spectator", seeded.administrator, {"password": PASSWORD})
    assert created.status_code == status.HTTP_200_OK
    assert created.json()["configured"] is True

    duplicate = call("POST", "/api/admin/spectator", seeded.administrator, {"password": PASSWORD})
    assert duplicate.status_code == status.HTTP_409_CONFLICT

    with SessionFactory() as session:
        spectators = list(
            session.scalars(
                select(User).where(
                    User.workspace_id == seeded.workspace_id, User.role == UserRole.SPECTATOR
                )
            )
        )

    assert len(spectators) == 1
    assert spectators[0].email is None


@pytest.mark.parametrize("password", ["x" * 13, "x" * 129])
def test_password_bounds_are_enforced(seed: Callable[..., Seeded], password: str) -> None:
    seeded = seed()

    response = call(
        "POST", "/api/admin/spectator/reset-password", seeded.administrator, {"password": password}
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert password not in response.text


def test_reset_revokes_spectator_sessions_and_old_password(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    assert seeded.spectator_id is not None

    with TestClient(app) as first, TestClient(app) as second:
        assert spectator_login(seeded.slug, PASSWORD, first).status_code == 200
        assert spectator_login(seeded.slug, PASSWORD, second).status_code == 200
        # Concurrent spectator sessions remain allowed.
        assert first.get("/api/auth/me").status_code == 200
        assert second.get("/api/auth/me").status_code == 200

        response = call(
            "POST",
            "/api/admin/spectator/reset-password",
            seeded.administrator,
            {"password": NEW_PASSWORD},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["revoked_sessions"] == 2
        assert_no_leak(response.text, seeded, PASSWORD, NEW_PASSWORD)
        assert first.get("/api/auth/me").status_code == 401
        assert second.get("/api/auth/me").status_code == 401

    reasons = {row.revocation_reason for row in spectator_sessions(seeded.spectator_id)}
    assert reasons == {SessionRevocationReason.SPECTATOR_PASSWORD_RESET}

    old = spectator_login(seeded.slug, PASSWORD)
    assert old.status_code == 401
    assert old.json()["detail"] == GENERIC_SPECTATOR_FAILURE
    assert spectator_login(seeded.slug, NEW_PASSWORD).status_code == 200

    # The administrator's own session is untouched.
    assert call("GET", "/api/admin/spectator", seeded.administrator).status_code == 200


def test_disable_and_reenable_with_new_password(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    assert seeded.spectator_id is not None

    with TestClient(app) as viewer:
        assert spectator_login(seeded.slug, PASSWORD, viewer).status_code == 200

        disabled = call(
            "PATCH", "/api/admin/spectator/access", seeded.administrator, {"is_active": False}
        )

        assert disabled.status_code == status.HTTP_200_OK
        assert disabled.json() == {
            "workspace_slug": seeded.slug,
            "configured": True,
            "enabled": False,
            "revoked_sessions": 1,
        }
        assert viewer.get("/api/auth/me").status_code == 401

    assert {row.revocation_reason for row in spectator_sessions(seeded.spectator_id)} == {
        SessionRevocationReason.SPECTATOR_DISABLED
    }

    blocked = spectator_login(seeded.slug, PASSWORD)
    assert blocked.status_code == 401
    assert blocked.json()["detail"] == GENERIC_SPECTATOR_FAILURE

    missing_password = call(
        "PATCH", "/api/admin/spectator/access", seeded.administrator, {"is_active": True}
    )
    assert missing_password.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    enabled = call(
        "PATCH",
        "/api/admin/spectator/access",
        seeded.administrator,
        {"is_active": True, "password": NEW_PASSWORD},
    )
    assert enabled.status_code == status.HTTP_200_OK
    assert enabled.json()["enabled"] is True
    assert_no_leak(enabled.text, seeded, PASSWORD, NEW_PASSWORD)

    assert spectator_login(seeded.slug, PASSWORD).status_code == 401
    assert spectator_login(seeded.slug, NEW_PASSWORD).status_code == 200
    assert call("GET", "/api/admin/spectator", seeded.administrator).status_code == 200


def test_management_is_scoped_to_the_administrators_workspace(
    seed: Callable[..., Seeded],
) -> None:
    mine = seed()
    other = seed()
    assert other.spectator_id is not None

    call("PATCH", "/api/admin/spectator/access", mine.administrator, {"is_active": False})

    with SessionFactory() as session:
        untouched = session.get(User, other.spectator_id)
        assert untouched is not None and untouched.is_active is True

    assert spectator_login(other.slug, PASSWORD).status_code == 200


@pytest.mark.parametrize(
    "field_name", ["workspace_id", "user_id", "spectator_id", "username", "email"]
)
def test_body_identifiers_are_rejected(seed: Callable[..., Seeded], field_name: str) -> None:
    seeded = seed()

    response = call(
        "POST",
        "/api/admin/spectator/reset-password",
        seeded.administrator,
        {"password": NEW_PASSWORD, field_name: 1},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.parametrize(
    ("registration_status", "installation_status", "code"),
    [
        (WorkspaceRegistrationStatus.SUSPENDED, InstallationStatus.ACTIVE, "workspace_suspended"),
        (WorkspaceRegistrationStatus.DELETED, InstallationStatus.ACTIVE, "workspace_inactive"),
        (
            WorkspaceRegistrationStatus.DELETION_PENDING,
            InstallationStatus.ACTIVE,
            "workspace_inactive",
        ),
        (
            WorkspaceRegistrationStatus.PROVISIONAL,
            InstallationStatus.SETUP_IN_PROGRESS,
            "workspace_inactive",
        ),
    ],
)
def test_management_requires_an_active_workspace(
    seed: Callable[..., Seeded],
    registration_status: WorkspaceRegistrationStatus,
    installation_status: InstallationStatus,
    code: str,
) -> None:
    seeded = seed(registration_status=registration_status, installation_status=installation_status)

    for method, path, body in (
        ("GET", "/api/admin/spectator", None),
        ("POST", "/api/admin/spectator/reset-password", {"password": NEW_PASSWORD}),
        ("PATCH", "/api/admin/spectator/access", {"is_active": False}),
    ):
        response = call(method, path, seeded.administrator, body)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["code"] == code


def test_spectators_cannot_manage_spectator_access(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    with TestClient(app) as viewer:
        login = spectator_login(seeded.slug, PASSWORD, viewer)
        csrf = login.json()["csrf_token"]
        response = viewer.patch(
            "/api/admin/spectator/access",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf},
            json={"is_active": False},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
