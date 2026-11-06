from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.auth.security import (
    hash_password,
    password_hash_needs_update,
    verify_password,
)
from app.db.auth_models import AuthSession, User
from app.repositories.auth_repository import (
    AuthenticationRepository,
)
from app.repositories.session_repository import (
    CreatedSession,
    ResolvedSession,
    SessionRepository,
)


class InvalidCredentialsError(ValueError):
    pass


class InactiveAccountError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    auth_session: AuthSession
    session_token: str
    csrf_token: str


def authenticate_user(
    session: Session,
    *,
    username: str,
    password: str,
) -> User:
    repository = AuthenticationRepository(session)

    try:
        user = repository.get_user_by_username(username)
    except ValueError:
        user = None

    if user is None:
        _perform_dummy_password_check(password)

        raise InvalidCredentialsError("Invalid username or password.")

    if not verify_password(
        password,
        user.password_hash,
    ):
        raise InvalidCredentialsError("Invalid username or password.")

    if not user.is_active:
        raise InactiveAccountError("This account is inactive.")

    if password_hash_needs_update(user.password_hash):
        user.password_hash = hash_password(password)

    return user


def log_in_user(
    session: Session,
    *,
    username: str,
    password: str,
    session_lifetime_hours: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> LoginResult:
    user = authenticate_user(
        session,
        username=username,
        password=password,
    )

    authentication_repository = AuthenticationRepository(session)
    authentication_repository.record_successful_login(user)

    session_repository = SessionRepository(session)

    created: CreatedSession = session_repository.create_session(
        user=user,
        lifetime_hours=session_lifetime_hours,
        ip_address=ip_address,
        user_agent=user_agent,
        now=now,
    )

    session.flush()

    return LoginResult(
        user=user,
        auth_session=created.session,
        session_token=created.session_token,
        csrf_token=created.csrf_token,
    )


def resolve_authenticated_user(
    session: Session,
    *,
    session_token: str,
    now: datetime | None = None,
) -> ResolvedSession | None:
    repository = SessionRepository(session)

    return repository.resolve_session(
        session_token,
        now=now,
    )


def log_out_user(
    session: Session,
    *,
    session_token: str,
    now: datetime | None = None,
) -> bool:
    repository = SessionRepository(session)

    return repository.revoke_session_by_token(
        session_token,
        now=now,
    )


def _perform_dummy_password_check(
    password: str,
) -> None:
    verify_password(
        password,
        _dummy_password_hash(),
    )


def _dummy_password_hash() -> str:
    return (
        "$argon2id$v=19$m=65536,t=3,p=4$"
        "c29tZXNhbHQxMjM0NTY3OA$"
        "o2HU8CREdyDg6f6xqml4XSrV1bF0YC"
        "tf5ekp1SjeKIU"
    )
