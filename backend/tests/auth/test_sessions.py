from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.authentication import (
    InactiveAccountError,
    InvalidCredentialsError,
    authenticate_user,
    log_in_user,
    log_out_user,
    resolve_authenticated_user,
)
from app.auth.security import (
    hash_password,
    hash_token,
)
from app.db.auth_models import AuthSession, User
from app.db.session import engine
from app.repositories.auth_repository import (
    AuthenticationRepository,
)
from app.repositories.session_repository import (
    SessionRepository,
)


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def create_test_user(
    session: Session,
    *,
    username: str = "session-user",
    password: str = "correct horse battery staple",
    is_active: bool = True,
) -> User:
    repository = AuthenticationRepository(session)

    return repository.create_user(
        username=username,
        display_name="Session User",
        password=password,
        is_active=is_active,
    )


def test_login_creates_hashed_session_and_csrf_tokens(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)
    now = datetime.now(UTC)

    result = log_in_user(
        database_session,
        username=user.username,
        password="correct horse battery staple",
        session_lifetime_hours=168,
        ip_address="127.0.0.1",
        user_agent="pytest browser",
        now=now,
    )

    assert result.user.id == user.id
    assert result.session_token
    assert result.csrf_token

    assert result.auth_session.session_token_hash == hash_token(result.session_token)
    assert result.auth_session.csrf_token_hash == hash_token(result.csrf_token)
    assert result.auth_session.session_token_hash != result.session_token
    assert result.auth_session.expires_at == (now + timedelta(hours=168))
    assert result.auth_session.ip_address == "127.0.0.1"
    assert result.auth_session.user_agent == "pytest browser"


def test_valid_session_resolves_active_user(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    login = log_in_user(
        database_session,
        username=user.username,
        password="correct horse battery staple",
        session_lifetime_hours=168,
    )

    resolved = resolve_authenticated_user(
        database_session,
        session_token=login.session_token,
    )

    assert resolved is not None
    assert resolved.user.id == user.id
    assert resolved.session.id == login.auth_session.id


def test_invalid_password_is_rejected(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    with pytest.raises(
        InvalidCredentialsError,
        match="Invalid username or password",
    ):
        authenticate_user(
            database_session,
            username=user.username,
            password="incorrect password",
        )


def test_unknown_username_uses_generic_error(
    database_session: Session,
) -> None:
    with pytest.raises(
        InvalidCredentialsError,
        match="Invalid username or password",
    ):
        authenticate_user(
            database_session,
            username="unknown-user",
            password="incorrect password",
        )


def test_inactive_user_cannot_log_in(
    database_session: Session,
) -> None:
    user = create_test_user(
        database_session,
        is_active=False,
    )

    with pytest.raises(
        InactiveAccountError,
        match="inactive",
    ):
        log_in_user(
            database_session,
            username=user.username,
            password="correct horse battery staple",
            session_lifetime_hours=168,
        )


def test_logout_revokes_session(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    login = log_in_user(
        database_session,
        username=user.username,
        password="correct horse battery staple",
        session_lifetime_hours=168,
    )

    assert log_out_user(
        database_session,
        session_token=login.session_token,
    )

    resolved = resolve_authenticated_user(
        database_session,
        session_token=login.session_token,
    )

    assert resolved is None


def test_expired_session_is_rejected(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)
    now = datetime.now(UTC)

    login = log_in_user(
        database_session,
        username=user.username,
        password="correct horse battery staple",
        session_lifetime_hours=1,
        now=now,
    )

    resolved = resolve_authenticated_user(
        database_session,
        session_token=login.session_token,
        now=now + timedelta(hours=2),
    )

    assert resolved is None


def test_inactive_user_invalidates_existing_session(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    login = log_in_user(
        database_session,
        username=user.username,
        password="correct horse battery staple",
        session_lifetime_hours=168,
    )

    user.is_active = False
    database_session.flush()

    resolved = resolve_authenticated_user(
        database_session,
        session_token=login.session_token,
    )

    assert resolved is None


def test_all_user_sessions_can_be_revoked(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    first = log_in_user(
        database_session,
        username=user.username,
        password="correct horse battery staple",
        session_lifetime_hours=168,
    )
    second = log_in_user(
        database_session,
        username=user.username,
        password="correct horse battery staple",
        session_lifetime_hours=168,
    )

    repository = SessionRepository(database_session)

    revoked_count = repository.revoke_all_user_sessions(
        user_id=user.id,
    )

    assert revoked_count == 2
    assert repository.resolve_session(first.session_token) is None
    assert repository.resolve_session(second.session_token) is None


def test_expired_sessions_can_be_deleted(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)
    now = datetime.now(UTC)

    login = log_in_user(
        database_session,
        username=user.username,
        password="correct horse battery staple",
        session_lifetime_hours=1,
        now=now,
    )

    repository = SessionRepository(database_session)

    deleted_count = repository.delete_expired_sessions(
        now=now + timedelta(hours=2),
    )

    assert deleted_count == 1

    stored_session = database_session.scalar(
        select(AuthSession).where(AuthSession.id == login.auth_session.id)
    )

    assert stored_session is None


def test_password_hash_is_argon2(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    assert user.password_hash.startswith("$argon2")
    assert user.password_hash != hash_password("correct horse battery staple")
