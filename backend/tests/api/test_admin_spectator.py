from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth_dependencies import (
    get_current_user,
    require_session_csrf,
)
from app.api.security import validate_request_origin
from app.db.auth_models import User, UserRole
from app.db.session import get_database_session
from app.main import app

client = TestClient(app)


def admin_user() -> User:
    return User(
        id=100,
        username="admin-user",
        display_name="Administrator",
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def spectator_user(
    *,
    active: bool = True,
) -> User:
    return User(
        id=200,
        username="league-viewer",
        display_name="League Viewer",
        email="viewer@example.com",
        password_hash="$argon2id$test-placeholder",
        role=UserRole.SPECTATOR,
        is_active=active,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def override_admin() -> User:
    return admin_user()


def override_csrf() -> None:
    return None


def override_origin() -> None:
    return None


def session_override(
    session: MagicMock,
):
    def override() -> Iterator[Session]:
        yield session

    return override


def apply_overrides(
    session: MagicMock,
) -> None:
    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[validate_request_origin] = override_origin
    app.dependency_overrides[get_database_session] = session_override(session)


def clear_overrides() -> None:
    for dependency in (
        get_current_user,
        require_session_csrf,
        validate_request_origin,
        get_database_session,
    ):
        app.dependency_overrides.pop(
            dependency,
            None,
        )


def test_read_missing_spectator() -> None:
    session = MagicMock()
    apply_overrides(session)

    with patch(
        "app.api.admin.get_spectator_account",
        return_value=None,
    ):
        try:
            response = client.get("/api/admin/spectator")
        finally:
            clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "configured": False,
        "id": None,
        "username": None,
        "display_name": None,
        "email": None,
        "is_active": None,
    }


def test_admin_can_create_spectator() -> None:
    session = MagicMock()
    stored = spectator_user()
    apply_overrides(session)

    with patch(
        "app.api.admin.create_spectator_account",
        return_value=stored,
    ) as create:
        try:
            response = client.post(
                "/api/admin/spectator",
                json={
                    "username": "league-viewer",
                    "display_name": "League Viewer",
                    "email": "viewer@example.com",
                    "password": ("correct horse battery staple"),
                },
            )
        finally:
            clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "configured": True,
        "id": 200,
        "username": "league-viewer",
        "display_name": "League Viewer",
        "email": "viewer@example.com",
        "is_active": True,
        "revoked_sessions": 0,
    }

    create.assert_called_once_with(
        session,
        username="league-viewer",
        display_name="League Viewer",
        email="viewer@example.com",
        password="correct horse battery staple",
    )
    session.commit.assert_called_once()


def test_password_reset_returns_revoked_count() -> None:
    session = MagicMock()
    stored = spectator_user()
    apply_overrides(session)

    with patch(
        "app.api.admin.reset_spectator_password",
        return_value=(stored, 3),
    ):
        try:
            response = client.post(
                "/api/admin/spectator/reset-password",
                json={
                    "password": ("a new spectator password"),
                },
            )
        finally:
            clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["revoked_sessions"] == 3
    session.commit.assert_called_once()


def test_disabling_access_returns_revoked_count() -> None:
    session = MagicMock()
    stored = spectator_user(active=False)
    apply_overrides(session)

    with patch(
        "app.api.admin.set_spectator_active",
        return_value=(stored, 2),
    ):
        try:
            response = client.patch(
                "/api/admin/spectator/access",
                json={
                    "is_active": False,
                },
            )
        finally:
            clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is False
    assert response.json()["revoked_sessions"] == 2


def test_spectator_response_exposes_no_secrets() -> None:
    session = MagicMock()
    stored = spectator_user()
    apply_overrides(session)

    with patch(
        "app.api.admin.get_spectator_account",
        return_value=stored,
    ):
        try:
            response = client.get("/api/admin/spectator")
        finally:
            clear_overrides()

    serialized = response.text.casefold()

    assert "password_hash" not in serialized
    assert "session_token" not in serialized
    assert "csrf" not in serialized
