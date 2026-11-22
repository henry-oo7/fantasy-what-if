import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status

from app.api.auth_dependencies import (
    WorkspaceAccessError,
    require_active_workspace,
)
from app.db.auth_models import AuthSession, User, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.workspace_access_errors import (
    workspace_access_exception_handler,
)


def user(
    *,
    role: UserRole = UserRole.ADMIN,
    verified: bool = True,
) -> User:
    return User(
        id=17,
        username="workspace-gate-user",
        display_name="Workspace Gate User",
        workspace_id=42,
        email="workspace-gate@example.com",
        normalized_email="workspace-gate@example.com",
        email_verified_at=(datetime.now(UTC) if verified else None),
        password_hash="$argon2id$test-placeholder",
        role=role,
        is_active=True,
    )


def workspace(
    *,
    installation_status: InstallationStatus = (InstallationStatus.SETUP_IN_PROGRESS),
    registration_status: (
        WorkspaceRegistrationStatus | None
    ) = WorkspaceRegistrationStatus.PROVISIONAL,
) -> Installation:
    return Installation(
        id=42,
        application_name="Workspace Gate Test",
        slug="workspace-gate-test",
        normalized_slug="workspace-gate-test",
        timezone="America/Chicago",
        status=installation_status,
        registration_status=registration_status,
        onboarding_stage=(OnboardingStage.PROVIDER_SETUP_PENDING),
    )


class FakeSession:
    def __init__(
        self,
        *,
        stored_user: User | None,
        stored_workspace: Installation | None,
    ) -> None:
        self.stored_user = stored_user
        self.stored_workspace = stored_workspace

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> None:
        del exc_type, exc, traceback

    def get(
        self,
        model,
        object_id: int,
    ):
        del object_id

        if model is User:
            return self.stored_user

        if model is Installation:
            return self.stored_workspace

        return None


def session_factory(
    *,
    stored_user: User | None,
    stored_workspace: Installation | None,
):
    return lambda: FakeSession(
        stored_user=stored_user,
        stored_workspace=stored_workspace,
    )


def test_legacy_active_workspace_is_allowed() -> None:
    stored_user = user()
    stored_workspace = workspace(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=None,
    )

    with patch(
        "app.api.auth_dependencies.SessionFactory",
        session_factory(
            stored_user=stored_user,
            stored_workspace=stored_workspace,
        ),
    ):
        assert require_active_workspace(stored_user) == 42


def test_saas_active_workspace_is_allowed() -> None:
    stored_user = user()
    stored_workspace = workspace(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=(WorkspaceRegistrationStatus.ACTIVE),
    )

    with patch(
        "app.api.auth_dependencies.SessionFactory",
        session_factory(
            stored_user=stored_user,
            stored_workspace=stored_workspace,
        ),
    ):
        assert require_active_workspace(stored_user) == 42


def test_provisional_workspace_is_blocked_from_active_routes() -> None:
    stored_user = user()
    stored_workspace = workspace()

    with (
        patch(
            "app.api.auth_dependencies.SessionFactory",
            session_factory(
                stored_user=stored_user,
                stored_workspace=stored_workspace,
            ),
        ),
        pytest.raises(
            WorkspaceAccessError,
        ) as exc_info,
    ):
        require_active_workspace(stored_user)

    assert exc_info.value.code == "workspace_inactive"


def test_suspended_workspace_returns_safe_code() -> None:
    stored_user = user()
    stored_workspace = workspace(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=(WorkspaceRegistrationStatus.SUSPENDED),
    )

    with (
        patch(
            "app.api.auth_dependencies.SessionFactory",
            session_factory(
                stored_user=stored_user,
                stored_workspace=stored_workspace,
            ),
        ),
        pytest.raises(
            WorkspaceAccessError,
        ) as exc_info,
    ):
        require_active_workspace(stored_user)

    assert exc_info.value.code == "workspace_suspended"
    assert exc_info.value.detail == ("Workspace access is unavailable.")


def test_workspace_access_error_uses_stable_response() -> None:
    response = asyncio.run(
        workspace_access_exception_handler(
            MagicMock(),
            WorkspaceAccessError(
                detail="Workspace access is unavailable.",
                code="workspace_suspended",
            ),
        )
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert json.loads(response.body) == {
        "detail": "Workspace access is unavailable.",
        "code": "workspace_suspended",
        "retryable": False,
    }


def test_auth_session_shape_remains_unchanged() -> None:
    timestamp = datetime.now(UTC)

    auth_session = AuthSession(
        id=29,
        user_id=17,
        session_token_hash="a" * 64,
        csrf_token_hash="b" * 64,
        created_at=timestamp,
        expires_at=timestamp + timedelta(hours=1),
        last_seen_at=timestamp,
    )

    assert auth_session.user_id == 17
