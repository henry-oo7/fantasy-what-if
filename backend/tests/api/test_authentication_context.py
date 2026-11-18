from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, status

from app.api.auth_dependencies import (
    build_authentication_context,
)
from app.db.auth_models import AuthSession, User, UserRole
from app.repositories.session_repository import ResolvedSession


def resolved_session(
    *,
    workspace_id: int | None,
    role: UserRole = UserRole.SPECTATOR,
) -> ResolvedSession:
    now = datetime.now(UTC)

    user = User(
        id=17,
        username="context-user",
        display_name="Context User",
        workspace_id=workspace_id,
        email=None,
        normalized_email=None,
        password_hash="$argon2id$test-placeholder",
        role=role,
        is_active=True,
    )
    auth_session = AuthSession(
        id=29,
        user_id=user.id,
        session_token_hash="a" * 64,
        csrf_token_hash="b" * 64,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
    )

    return ResolvedSession(
        user=user,
        session=auth_session,
    )


def test_context_contains_explicit_workspace_identity() -> None:
    resolved = resolved_session(
        workspace_id=42,
        role=UserRole.ADMIN,
    )

    context = build_authentication_context(resolved)

    assert context.user_id == 17
    assert context.workspace_id == 42
    assert context.role is UserRole.ADMIN
    assert context.session_id == 29
    assert context.user is resolved.user
    assert context.auth_session is resolved.session


def test_tenantless_session_fails_closed() -> None:
    resolved = resolved_session(
        workspace_id=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        build_authentication_context(resolved)

    assert exc_info.value.status_code == (status.HTTP_401_UNAUTHORIZED)
    assert exc_info.value.detail == ("Authentication is required.")
