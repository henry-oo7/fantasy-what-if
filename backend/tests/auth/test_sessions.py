from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.authentication import (
    log_out_user,
    resolve_authenticated_user,
)
from app.auth.security import (
    hash_password,
    hash_token,
)
from app.db.auth_models import (
    AuthSession,
    SessionRevocationReason,
    User,
    UserRole,
)
from app.db.session import engine
from app.repositories.auth_repository import (
    AuthenticationRepository,
)
from app.repositories.session_repository import (
    CreatedSession,
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


def open_session(
    session: Session,
    *,
    user: User,
    session_lifetime_hours: int = 168,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> CreatedSession:
    """Open a session the way the email and slug login services do."""

    repository = SessionRepository(session)
    create = (
        repository.replace_user_sessions
        if user.role is UserRole.ADMIN
        else repository.create_session
    )

    return create(
        user=user,
        lifetime_hours=session_lifetime_hours,
        ip_address=ip_address,
        user_agent=user_agent,
        now=now,
    )


def test_session_creation_stores_hashed_session_and_csrf_tokens(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)
    now = datetime.now(UTC)

    result = open_session(
        database_session,
        user=user,
        session_lifetime_hours=168,
        ip_address="127.0.0.1",
        user_agent="pytest browser",
        now=now,
    )

    assert result.session.user_id == user.id
    assert result.session_token
    assert result.csrf_token

    assert result.session.session_token_hash == hash_token(result.session_token)
    assert result.session.csrf_token_hash == hash_token(result.csrf_token)
    assert result.session.session_token_hash != result.session_token
    assert result.session.expires_at == (now + timedelta(hours=168))
    assert result.session.ip_address == "127.0.0.1"
    assert result.session.user_agent == "pytest browser"


def test_valid_session_resolves_active_user(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    login = open_session(
        database_session,
        user=user,
        session_lifetime_hours=168,
    )

    resolved = resolve_authenticated_user(
        database_session,
        session_token=login.session_token,
    )

    assert resolved is not None
    assert resolved.user.id == user.id
    assert resolved.session.id == login.session.id


def test_logout_revokes_session(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    login = open_session(
        database_session,
        user=user,
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

    login = open_session(
        database_session,
        user=user,
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

    login = open_session(
        database_session,
        user=user,
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

    first = open_session(
        database_session,
        user=user,
        session_lifetime_hours=168,
    )
    second = open_session(
        database_session,
        user=user,
        session_lifetime_hours=168,
    )

    repository = SessionRepository(database_session)

    revoked_count = repository.revoke_all_user_sessions(
        user_id=user.id,
        reason=SessionRevocationReason.PASSWORD_RESET,
    )

    assert revoked_count == 2
    assert repository.resolve_session(first.session_token) is None
    assert repository.resolve_session(second.session_token) is None
    assert first.session.revocation_reason is SessionRevocationReason.PASSWORD_RESET
    assert second.session.revocation_reason is SessionRevocationReason.PASSWORD_RESET


def test_expired_sessions_can_be_deleted(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)
    now = datetime.now(UTC)
    expiration_cutoff = now + timedelta(hours=2)

    existing_expired_count = database_session.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.expires_at <= expiration_cutoff,
        )
    )

    assert existing_expired_count is not None

    login = open_session(
        database_session,
        user=user,
        session_lifetime_hours=1,
        now=now,
    )

    repository = SessionRepository(database_session)

    deleted_count = repository.delete_expired_sessions(
        now=expiration_cutoff,
    )

    assert deleted_count == existing_expired_count + 1

    stored_session = database_session.scalar(
        select(AuthSession).where(AuthSession.id == login.session.id)
    )

    assert stored_session is None


def test_password_hash_is_argon2(
    database_session: Session,
) -> None:
    user = create_test_user(database_session)

    assert user.password_hash.startswith("$argon2")
    assert user.password_hash != hash_password("correct horse battery staple")


def test_administrator_session_replaces_prior_session(
    database_session: Session,
) -> None:
    user = AuthenticationRepository(database_session).create_user(
        username="replacement-admin",
        display_name="Replacement Administrator",
        password="correct horse battery staple",
        role=UserRole.ADMIN,
    )

    first = open_session(
        database_session,
        user=user,
        session_lifetime_hours=168,
    )
    second = open_session(
        database_session,
        user=user,
        session_lifetime_hours=168,
    )

    repository = SessionRepository(database_session)

    assert repository.resolve_session(first.session_token) is None
    assert repository.resolve_session(second.session_token) is not None


def test_spectator_sessions_remain_concurrent(
    database_session: Session,
) -> None:
    user = create_test_user(
        database_session,
        username="concurrent-spectator",
    )

    first = open_session(
        database_session,
        user=user,
        session_lifetime_hours=168,
    )
    second = open_session(
        database_session,
        user=user,
        session_lifetime_hours=168,
    )

    repository = SessionRepository(database_session)

    assert repository.resolve_session(first.session_token) is not None
    assert repository.resolve_session(second.session_token) is not None
