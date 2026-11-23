from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.login_throttle_policy import (
    SOURCE_SPRAY_MAXIMUM_FAILURES,
    resolve_login_throttle_policy,
    resolve_source_spray_policy,
)
from app.auth.security import (
    hash_password,
    password_hash_needs_update,
    verify_password,
)
from app.db.auth_models import (
    AuthSession,
    AuthSessionPurpose,
    SessionRevocationReason,
    User,
    UserRole,
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
    ResolvedSession,
    SessionRepository,
)
from app.services.administrator_registration import (
    InvalidWorkspaceSlugError,
    normalize_workspace_slug,
)
from app.services.workspace_lifecycle import (
    is_active_workspace,
    is_deletion_pending,
    is_resumable_onboarding,
)

# FR-20: a deletion_recovery session lasts 15 minutes and is never renewed.
DELETION_RECOVERY_SESSION_LIFETIME = timedelta(minutes=15)


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

    @property
    def session_purpose(self) -> AuthSessionPurpose:
        return self.auth_session.purpose


def lock_workspace_for_login(
    session: Session,
    workspace_id: int,
) -> Installation | None:
    """Read the workspace under ``FOR SHARE`` before creating a session.

    The FR-20 deletion request holds ``FOR UPDATE`` on the same row while
    it revokes every workspace session. A login that waits here observes
    the committed lifecycle afterwards, and a login that locks first
    commits its session before the request can start revoking. Either
    way no standard session survives a deletion request. The lock is
    taken before any user-row write to keep a single lock order
    (workspace, then user) with the deletion request.
    """

    return session.scalar(
        select(Installation)
        .where(Installation.id == workspace_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
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
        reason=SessionRevocationReason.LOGOUT,
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

    workspace = lock_workspace_for_login(
        session,
        candidate_user.workspace_id,
    )
    current_time = now or datetime.now(UTC)

    # A deletion-pending workspace permits only a restricted recovery
    # session, and only before the scheduled deletion time (FR-20).
    deletion_recovery = (
        is_deletion_pending(workspace)
        and workspace is not None
        and workspace.deletion_scheduled_for is not None
        and current_time < workspace.deletion_scheduled_for
    )

    if (
        not deletion_recovery
        and not is_active_workspace(workspace)
        and not is_resumable_onboarding(workspace)
    ):
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

    repository_sessions = SessionRepository(session)

    if deletion_recovery:
        # Every earlier session was revoked by the deletion request; this
        # only replaces an earlier recovery session so at most one
        # administrator session stays active. It is never a standard
        # session and never restores a revoked one.
        created = repository_sessions.replace_user_sessions(
            user=candidate_user,
            lifetime=DELETION_RECOVERY_SESSION_LIFETIME,
            purpose=AuthSessionPurpose.DELETION_RECOVERY,
            ip_address=ip_address,
            user_agent=user_agent,
            now=current_time,
        )
    else:
        created = repository_sessions.replace_user_sessions(
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

    active_workspace = is_active_workspace(workspace)

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

    if spectator is None or workspace is None:
        raise InvalidCredentialsError("The workspace or spectator credentials are incorrect.")

    # Recheck under the workspace share lock so a concurrent deletion
    # request either revokes this session or is observed here.
    if not is_active_workspace(lock_workspace_for_login(session, workspace.id)):
        _record_login_failure(
            throttle=throttle,
            policy=policy,
            source_throttle=source_throttle,
            source_policy=source_policy,
            now=now,
        )
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
