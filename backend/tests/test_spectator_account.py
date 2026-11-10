from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.auth.security import verify_password
from app.db.auth_models import User, UserRole
from app.repositories.auth_repository import (
    AuthenticationRepository,
)
from app.services.spectator_account import (
    SPECTATOR_ACCOUNT_LOCK_KEY,
    SpectatorAccountStateError,
    SpectatorAlreadyExistsError,
    SpectatorNotFoundError,
    create_spectator_account,
    get_spectator_account,
    reset_spectator_password,
    set_spectator_active,
    update_spectator_profile,
)


def spectator(
    *,
    user_id: int = 20,
    is_active: bool = True,
) -> User:
    return User(
        id=user_id,
        username="league-viewer",
        display_name="League Viewer",
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=UserRole.SPECTATOR,
        is_active=is_active,
    )


def setup_session(
    *,
    spectators: list[User],
) -> MagicMock:
    session = MagicMock()
    session.scalars.return_value.all.return_value = spectators

    return session


def test_get_spectator_returns_none_when_missing() -> None:
    session = setup_session(spectators=[])

    assert get_spectator_account(session) is None


def test_multiple_spectators_are_rejected() -> None:
    session = setup_session(
        spectators=[
            spectator(user_id=20),
            spectator(user_id=21),
        ]
    )

    with pytest.raises(
        SpectatorAccountStateError,
        match="Multiple spectator",
    ):
        get_spectator_account(session)


def test_create_spectator_uses_auth_repository() -> None:
    session = setup_session(spectators=[])
    created = spectator()

    with patch("app.services.spectator_account.AuthenticationRepository") as repository_type:
        repository_type.return_value.create_user.return_value = created

        result = create_spectator_account(
            session,
            username="league-viewer",
            display_name="League Viewer",
            email="viewer@example.com",
            password="correct horse battery staple",
        )

    assert result is created

    repository_type.return_value.create_user.assert_called_once_with(
        username="league-viewer",
        display_name="League Viewer",
        email="viewer@example.com",
        password="correct horse battery staple",
        role=UserRole.SPECTATOR,
        is_active=True,
    )

    statement = str(session.execute.call_args.args[0])

    assert "pg_advisory_xact_lock" in statement
    assert session.execute.call_args.args[1] == {
        "lock_key": SPECTATOR_ACCOUNT_LOCK_KEY,
    }


def test_existing_spectator_blocks_creation() -> None:
    session = setup_session(spectators=[spectator()])

    with pytest.raises(
        SpectatorAlreadyExistsError,
        match="already configured",
    ):
        create_spectator_account(
            session,
            username="second-viewer",
            display_name="Second Viewer",
            password="correct horse battery staple",
        )


def test_profile_update_normalizes_values() -> None:
    stored = spectator()
    session = setup_session(spectators=[stored])

    with patch.object(
        AuthenticationRepository,
        "get_user_by_email",
        return_value=None,
    ):
        result = update_spectator_profile(
            session,
            display_name="  Shared   League Viewer  ",
            email="  VIEWER@EXAMPLE.COM  ",
        )

    assert result is stored
    assert stored.display_name == ("Shared League Viewer")
    assert stored.email == "viewer@example.com"
    session.flush.assert_called_once()


def test_password_reset_hashes_and_revokes_sessions() -> None:
    stored = spectator()
    session = setup_session(spectators=[stored])
    timestamp = datetime(
        2026,
        11,
        9,
        18,
        0,
        tzinfo=UTC,
    )

    with patch("app.services.spectator_account.SessionRepository") as session_repository_type:
        session_repository_type.return_value.revoke_all_user_sessions.return_value = 3

        result, revoked = reset_spectator_password(
            session,
            password="a new spectator password",
            now=timestamp,
        )

    assert result is stored
    assert revoked == 3
    assert stored.password_hash.startswith("$argon2")
    assert verify_password(
        "a new spectator password",
        stored.password_hash,
    )

    session_repository_type.return_value.revoke_all_user_sessions.assert_called_once_with(
        user_id=stored.id,
        now=timestamp,
    )


def test_disabling_spectator_revokes_sessions() -> None:
    stored = spectator()
    session = setup_session(spectators=[stored])

    with patch("app.services.spectator_account.SessionRepository") as session_repository_type:
        session_repository_type.return_value.revoke_all_user_sessions.return_value = 2

        result, revoked = set_spectator_active(
            session,
            is_active=False,
        )

    assert result.is_active is False
    assert revoked == 2


def test_enabling_spectator_does_not_revoke_sessions() -> None:
    stored = spectator(is_active=False)
    session = setup_session(spectators=[stored])

    with patch("app.services.spectator_account.SessionRepository") as session_repository_type:
        result, revoked = set_spectator_active(
            session,
            is_active=True,
        )

    assert result.is_active is True
    assert revoked == 0
    session_repository_type.return_value.revoke_all_user_sessions.assert_not_called()


def test_missing_spectator_is_reported() -> None:
    session = setup_session(spectators=[])

    with pytest.raises(
        SpectatorNotFoundError,
        match="not configured",
    ):
        reset_spectator_password(
            session,
            password="a new spectator password",
        )
