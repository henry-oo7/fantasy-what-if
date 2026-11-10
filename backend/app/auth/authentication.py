from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.auth.login_throttle_policy import (
    SOURCE_SPRAY_MAXIMUM_FAILURES,
    resolve_login_throttle_policy,
    resolve_source_spray_policy,
)
from app.auth.security import (
    hash_password,
    normalize_username,
    password_hash_needs_update,
    verify_password,
)
from app.db.auth_models import AuthSession, User
from app.repositories.auth_repository import (
    AuthenticationRepository,
)
from app.repositories.login_attempt_repository import (
    LoginAttemptRepository,
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


class LoginThrottledError(ValueError):
    """The login key is temporarily locked."""

    def __init__(
        self,
        *,
        retry_after_seconds: int,
    ) -> None:
        self.retry_after_seconds = max(
            retry_after_seconds,
            1,
        )

        super().__init__("Invalid username or password.")


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
    throttle_secret: str | None = None,
    browser_id: str | None = None,
    maximum_attempts: int = 5,
    attempt_window_minutes: int = 15,
    lockout_minutes: int = 15,
) -> LoginResult:
    authentication_repository = AuthenticationRepository(session)

    try:
        normalized_username = normalize_username(username)
    except ValueError:
        normalized_username = username.strip().casefold()

        if not normalized_username:
            normalized_username = "unknown"

    try:
        candidate_user = authentication_repository.get_user_by_username(normalized_username)
    except ValueError:
        candidate_user = None

    policy = None
    source_policy = None
    throttle = None
    source_throttle = None

    if throttle_secret is not None:
        effective_browser_id = (
            browser_id.strip()
            if browser_id is not None and browser_id.strip()
            else "missing-browser-identifier"
        )

        policy = resolve_login_throttle_policy(
            normalized_username=normalized_username,
            user_role=(candidate_user.role if candidate_user is not None else None),
            browser_id=effective_browser_id,
            source_address=ip_address,
            secret=throttle_secret,
        )

        source_policy = resolve_source_spray_policy(
            source_address=ip_address,
            secret=throttle_secret,
        )

        throttle = LoginAttemptRepository(
            session,
            secret=throttle_secret,
            maximum_attempts=maximum_attempts,
            attempt_window_minutes=(attempt_window_minutes),
            lockout_minutes=lockout_minutes,
            delay_schedule_seconds=(policy.delay_schedule_seconds),
        )

        source_throttle = LoginAttemptRepository(
            session,
            secret=throttle_secret,
            maximum_attempts=(SOURCE_SPRAY_MAXIMUM_FAILURES),
            attempt_window_minutes=(attempt_window_minutes),
            lockout_minutes=30,
            delay_schedule_seconds=(source_policy.delay_schedule_seconds),
        )

        role_state = throttle.throttle_state_for_bucket(
            bucket_username=(policy.bucket_username),
            bucket_source=(policy.bucket_source),
            now=now,
        )

        source_state = source_throttle.throttle_state_for_bucket(
            bucket_username=(source_policy.bucket_username),
            bucket_source=(source_policy.bucket_source),
            now=now,
        )

        retry_after = max(
            role_state.retry_after_seconds,
            source_state.retry_after_seconds,
        )

        if retry_after > 0:
            _perform_dummy_password_check(password)

            raise LoginThrottledError(
                retry_after_seconds=retry_after,
            )

    try:
        user = authenticate_user(
            session,
            username=normalized_username,
            password=password,
        )
    except (
        InvalidCredentialsError,
        InactiveAccountError,
    ):
        role_failure = None
        source_failure = None

        if throttle is not None and policy is not None:
            role_failure = throttle.record_failure_for_bucket(
                bucket_username=(policy.bucket_username),
                bucket_source=(policy.bucket_source),
                now=now,
            )

        if source_throttle is not None and source_policy is not None:
            source_failure = source_throttle.record_failure_for_bucket(
                bucket_username=(source_policy.bucket_username),
                bucket_source=(source_policy.bucket_source),
                now=now,
            )

        retry_after = max(
            (role_failure.retry_after_seconds if role_failure is not None else 0),
            (source_failure.retry_after_seconds if source_failure is not None else 0),
        )

        if retry_after > 0:
            raise LoginThrottledError(
                retry_after_seconds=retry_after,
            ) from None

        raise

    if throttle is not None and policy is not None:
        throttle.clear_bucket(
            bucket_username=(policy.bucket_username),
            bucket_source=policy.bucket_source,
        )

    # Do not clear the network spray bucket after a
    # successful login. One valid password must not erase
    # evidence of failures against other usernames.

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
