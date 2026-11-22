from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
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
from app.db.auth_models import (
    AuthSession,
    User,
    UserRole,
)
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.repositories.auth_repository import (
    AuthenticationRepository,
    normalize_email,
)
from app.repositories.login_attempt_repository import (
    LoginAttemptRepository,
)
from app.repositories.session_repository import (
    CreatedSession,
    ResolvedSession,
    SessionRepository,
)
from app.services.administrator_registration import (
    InvalidWorkspaceSlugError,
    normalize_workspace_slug,
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

    if user.role is UserRole.ADMIN:
        session_repository.revoke_all_user_sessions(
            user_id=user.id,
            now=now,
        )

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


def log_in_administrator_by_email(
    session: Session,
    *,
    email: str,
    password: str,
    session_lifetime_hours: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
    throttle_secret: str | None = None,
    maximum_attempts: int = 5,
    attempt_window_minutes: int = 15,
    lockout_minutes: int = 15,
) -> LoginResult:
    """Authenticate one administrator by normalized email."""

    try:
        normalized_email = normalize_email(email)
    except ValueError:
        normalized_email = email.strip().casefold() or "unknown"

    repository = AuthenticationRepository(session)

    try:
        candidate_user = repository.get_administrator_by_email(normalized_email)
    except ValueError:
        candidate_user = None

    throttle = None
    source_throttle = None
    policy = None
    source_policy = None

    if throttle_secret is not None:
        policy = resolve_login_throttle_policy(
            normalized_username=normalized_email,
            user_role=UserRole.ADMIN,
            browser_id="administrator-email-login",
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
            attempt_window_minutes=attempt_window_minutes,
            lockout_minutes=lockout_minutes,
            delay_schedule_seconds=(policy.delay_schedule_seconds),
        )
        source_throttle = LoginAttemptRepository(
            session,
            secret=throttle_secret,
            maximum_attempts=SOURCE_SPRAY_MAXIMUM_FAILURES,
            attempt_window_minutes=attempt_window_minutes,
            lockout_minutes=30,
            delay_schedule_seconds=(source_policy.delay_schedule_seconds),
        )

        account_state = throttle.throttle_state_for_bucket(
            bucket_username=policy.bucket_username,
            bucket_source=policy.bucket_source,
            now=now,
        )
        source_state = source_throttle.throttle_state_for_bucket(
            bucket_username=source_policy.bucket_username,
            bucket_source=source_policy.bucket_source,
            now=now,
        )

        retry_after = max(
            account_state.retry_after_seconds,
            source_state.retry_after_seconds,
        )

        if retry_after > 0:
            _perform_dummy_password_check(password)

            raise LoginThrottledError(
                retry_after_seconds=retry_after,
            )

    authentication_failed = candidate_user is None or candidate_user.role is not UserRole.ADMIN

    if authentication_failed:
        _perform_dummy_password_check(password)
    elif not verify_password(
        password,
        candidate_user.password_hash,
    ):
        authentication_failed = True

    if authentication_failed:
        retry_after = _record_login_failure(
            throttle=throttle,
            policy=policy,
            source_throttle=source_throttle,
            source_policy=source_policy,
            now=now,
        )

        if retry_after > 0:
            raise LoginThrottledError(
                retry_after_seconds=retry_after,
            )

        raise InvalidCredentialsError("Invalid email or password.")

    if candidate_user is None:
        raise InvalidCredentialsError("Invalid email or password.")

    if (
        not candidate_user.is_active
        or candidate_user.email_verified_at is None
        or candidate_user.workspace_id is None
    ):
        _record_login_failure(
            throttle=throttle,
            policy=policy,
            source_throttle=source_throttle,
            source_policy=source_policy,
            now=now,
        )
        raise InvalidCredentialsError("Invalid email or password.")

    workspace = session.get(
        Installation,
        candidate_user.workspace_id,
    )

    active_workspace = (
        workspace is not None
        and workspace.status is InstallationStatus.ACTIVE
        and workspace.registration_status
        in (
            None,
            WorkspaceRegistrationStatus.ACTIVE,
        )
    )
    resumable_onboarding = (
        workspace is not None
        and workspace.status is not InstallationStatus.ACTIVE
        and workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
        and workspace.onboarding_stage
        not in (
            None,
            OnboardingStage.REGISTRATION_PENDING,
            OnboardingStage.EMAIL_VERIFICATION_PENDING,
        )
    )

    if not active_workspace and not resumable_onboarding:
        _record_login_failure(
            throttle=throttle,
            policy=policy,
            source_throttle=source_throttle,
            source_policy=source_policy,
            now=now,
        )
        raise InvalidCredentialsError("Invalid email or password.")

    if password_hash_needs_update(candidate_user.password_hash):
        candidate_user.password_hash = hash_password(password)

    if throttle is not None and policy is not None:
        throttle.clear_bucket(
            bucket_username=policy.bucket_username,
            bucket_source=policy.bucket_source,
        )

    repository.record_successful_login(candidate_user)

    created = SessionRepository(session).replace_user_sessions(
        user=candidate_user,
        lifetime_hours=session_lifetime_hours,
        ip_address=ip_address,
        user_agent=user_agent,
        now=now,
    )

    session.flush()

    return LoginResult(
        user=candidate_user,
        auth_session=created.session,
        session_token=created.session_token,
        csrf_token=created.csrf_token,
    )


def _record_login_failure(
    *,
    throttle: LoginAttemptRepository | None,
    policy,
    source_throttle: LoginAttemptRepository | None,
    source_policy,
    now: datetime | None,
) -> int:
    role_failure = None
    source_failure = None

    if throttle is not None and policy is not None:
        role_failure = throttle.record_failure_for_bucket(
            bucket_username=policy.bucket_username,
            bucket_source=policy.bucket_source,
            now=now,
        )

    if source_throttle is not None and source_policy is not None:
        source_failure = source_throttle.record_failure_for_bucket(
            bucket_username=source_policy.bucket_username,
            bucket_source=source_policy.bucket_source,
            now=now,
        )

    return max(
        (role_failure.retry_after_seconds if role_failure is not None else 0),
        (source_failure.retry_after_seconds if source_failure is not None else 0),
    )


def log_in_workspace_spectator(
    session: Session,
    *,
    workspace_slug: str,
    password: str,
    session_lifetime_hours: int,
    browser_id: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
    throttle_secret: str | None = None,
    maximum_attempts: int = 5,
    attempt_window_minutes: int = 15,
    lockout_minutes: int = 15,
) -> LoginResult:
    """Authenticate one workspace's shared spectator."""

    try:
        normalized_slug = normalize_workspace_slug(workspace_slug)
    except InvalidWorkspaceSlugError:
        normalized_slug = workspace_slug.strip().casefold() or "unknown-workspace"

    workspace = session.scalar(
        select(Installation).where(Installation.normalized_slug == normalized_slug)
    )

    spectator = None

    if workspace is not None:
        spectator = session.scalar(
            select(User).where(
                User.workspace_id == workspace.id,
                User.role == UserRole.SPECTATOR,
            )
        )

    throttle = None
    source_throttle = None
    policy = None
    source_policy = None

    if throttle_secret is not None:
        policy = resolve_login_throttle_policy(
            normalized_username=normalized_slug,
            user_role=UserRole.SPECTATOR,
            browser_id=browser_id,
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
            attempt_window_minutes=attempt_window_minutes,
            lockout_minutes=lockout_minutes,
            delay_schedule_seconds=(policy.delay_schedule_seconds),
        )
        source_throttle = LoginAttemptRepository(
            session,
            secret=throttle_secret,
            maximum_attempts=SOURCE_SPRAY_MAXIMUM_FAILURES,
            attempt_window_minutes=attempt_window_minutes,
            lockout_minutes=30,
            delay_schedule_seconds=(source_policy.delay_schedule_seconds),
        )

        spectator_state = throttle.throttle_state_for_bucket(
            bucket_username=policy.bucket_username,
            bucket_source=policy.bucket_source,
            now=now,
        )
        source_state = source_throttle.throttle_state_for_bucket(
            bucket_username=(source_policy.bucket_username),
            bucket_source=source_policy.bucket_source,
            now=now,
        )

        retry_after = max(
            spectator_state.retry_after_seconds,
            source_state.retry_after_seconds,
        )

        if retry_after > 0:
            _perform_dummy_password_check(password)

            raise LoginThrottledError(
                retry_after_seconds=retry_after,
            )

    active_workspace = (
        workspace is not None
        and workspace.status is InstallationStatus.ACTIVE
        and workspace.registration_status
        in (
            None,
            WorkspaceRegistrationStatus.ACTIVE,
        )
    )

    credentials_valid = (
        spectator is not None
        and spectator.role is UserRole.SPECTATOR
        and spectator.workspace_id == (workspace.id if workspace is not None else None)
        and spectator.is_active
        and verify_password(
            password,
            spectator.password_hash,
        )
    )

    if not active_workspace or not credentials_valid:
        if spectator is None:
            _perform_dummy_password_check(password)

        retry_after = _record_login_failure(
            throttle=throttle,
            policy=policy,
            source_throttle=source_throttle,
            source_policy=source_policy,
            now=now,
        )

        if retry_after > 0:
            raise LoginThrottledError(
                retry_after_seconds=retry_after,
            )

        raise InvalidCredentialsError("The workspace or spectator credentials are incorrect.")

    if spectator is None:
        raise InvalidCredentialsError("The workspace or spectator credentials are incorrect.")

    if password_hash_needs_update(spectator.password_hash):
        spectator.password_hash = hash_password(password)

    if throttle is not None and policy is not None:
        throttle.clear_bucket(
            bucket_username=policy.bucket_username,
            bucket_source=policy.bucket_source,
        )

    AuthenticationRepository(session).record_successful_login(spectator)

    created = SessionRepository(session).create_session(
        user=spectator,
        lifetime_hours=session_lifetime_hours,
        ip_address=ip_address,
        user_agent=user_agent,
        now=now,
    )

    session.flush()

    return LoginResult(
        user=spectator,
        auth_session=created.session,
        session_token=created.session_token,
        csrf_token=created.csrf_token,
    )
