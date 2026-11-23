"""FR-20 workspace deletion request, status, and restricted cancellation.

This module owns only the grace-period lifecycle. Final destructive
cleanup, deletion records, and claim, slug, email, connection, or
credential release belong to the later ``workspace_deletion`` worker.

Lock order is always workspace row, then administrator row, matching
administrator login, so the request, cancellation, login, and the
synchronization pre-commit recheck serialize on the workspace row.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import verify_password
from app.db.auth_models import (
    AuthSession,
    AuthSessionPurpose,
    SessionRevocationReason,
    User,
    UserRole,
)
from app.db.platform_enums import BackgroundJobType, WorkspaceRegistrationStatus
from app.db.platform_models import Installation
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.session_repository import SessionRepository
from app.services.administrator_registration import (
    InvalidWorkspaceSlugError,
    normalize_workspace_slug,
)
from app.services.workspace_lifecycle import is_active_workspace, is_deletion_pending

WORKSPACE_DELETION_GRACE_PERIOD_DAYS = 30
WORKSPACE_DELETION_GRACE_PERIOD = timedelta(days=WORKSPACE_DELETION_GRACE_PERIOD_DAYS)

WORKSPACE_DELETION_REQUEST_ACTION = "workspace_deletion_request"
WORKSPACE_DELETION_CANCEL_ACTION = "workspace_deletion_cancel"
WORKSPACE_DELETION_MAXIMUM_ATTEMPTS = 5
WORKSPACE_DELETION_ATTEMPT_WINDOW_MINUTES = 60


class WorkspaceDeletionStatus(StrEnum):
    ACTIVE = "active"
    DELETION_PENDING = "deletion_pending"


class WorkspaceDeletionError(RuntimeError):
    """Base error for deletion lifecycle refusals with a safe code."""

    code = "workspace_deletion_unavailable"


class WorkspaceDeletionUnavailableError(WorkspaceDeletionError):
    """The workspace, session, or administrator does not permit the action.

    Deliberately covers every state (not active, suspended, provisional,
    missing, stale session, past cutoff) with one safe outcome.
    """

    code = "workspace_deletion_unavailable"


class WorkspaceDeletionPasswordError(WorkspaceDeletionError):
    code = "invalid_current_password"


class WorkspaceDeletionSlugMismatchError(WorkspaceDeletionError):
    code = "workspace_slug_mismatch"


@dataclass(frozen=True, slots=True)
class WorkspaceDeletionState:
    """Safe deletion lifecycle view for the caller's own workspace."""

    status: WorkspaceDeletionStatus
    confirmation_slug: str | None = None
    requested_at: datetime | None = None
    scheduled_for: datetime | None = None
    cancellable: bool = False


def rate_limit_identity(*, workspace_id: int, user_id: int) -> str:
    """Rate-limit identity: administrator and workspace, never secrets."""

    return f"workspace:{workspace_id}:administrator:{user_id}"


def read_workspace_deletion_state(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    session_purpose: AuthSessionPurpose,
    now: datetime | None = None,
) -> WorkspaceDeletionState:
    """Return the deletion state the presented session may see."""

    current_time = now or datetime.now(UTC)
    workspace = session.get(Installation, workspace_id)
    administrator = session.get(User, user_id)

    if not _is_workspace_administrator(administrator, workspace_id=workspace_id):
        raise WorkspaceDeletionUnavailableError("Workspace deletion is unavailable.")

    if session_purpose is AuthSessionPurpose.STANDARD:
        if workspace is None or not is_active_workspace(workspace) or workspace.slug is None:
            raise WorkspaceDeletionUnavailableError("Workspace deletion is unavailable.")

        return WorkspaceDeletionState(
            status=WorkspaceDeletionStatus.ACTIVE,
            confirmation_slug=workspace.slug,
        )

    if (
        session_purpose is not AuthSessionPurpose.DELETION_RECOVERY
        or workspace is None
        or not is_deletion_pending(workspace)
        or workspace.deletion_requested_at is None
        or workspace.deletion_scheduled_for is None
    ):
        raise WorkspaceDeletionUnavailableError("Workspace deletion is unavailable.")

    return WorkspaceDeletionState(
        status=WorkspaceDeletionStatus.DELETION_PENDING,
        requested_at=workspace.deletion_requested_at,
        scheduled_for=workspace.deletion_scheduled_for,
        cancellable=current_time < workspace.deletion_scheduled_for,
    )


def request_workspace_deletion(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    auth_session_id: int,
    current_password: str,
    confirmation_slug: str,
    now: datetime | None = None,
) -> WorkspaceDeletionState:
    """Move an active workspace to ``deletion_pending`` in one transaction.

    The caller commits. A repeated or concurrent request that finds the
    workspace already pending returns the original schedule unchanged,
    without creating another job; its own session was revoked by the
    winning request, which is the only revocation it accepts.
    """

    current_time = now or datetime.now(UTC)
    workspace = _lock_workspace(session, workspace_id)
    administrator = _lock_administrator(session, user_id=user_id, workspace_id=workspace_id)

    if workspace is None or administrator is None:
        raise WorkspaceDeletionUnavailableError("Workspace deletion is unavailable.")

    already_pending = is_deletion_pending(workspace)
    _require_request_session(
        session,
        auth_session_id=auth_session_id,
        user_id=administrator.id,
        already_pending=already_pending,
        now=current_time,
    )

    if not verify_password(current_password, administrator.password_hash):
        raise WorkspaceDeletionPasswordError("The current password is incorrect.")

    if not _slug_matches(workspace, confirmation_slug):
        raise WorkspaceDeletionSlugMismatchError("The workspace name does not match.")

    if already_pending:
        return _pending_state(workspace, now=current_time)

    if not is_active_workspace(workspace):
        raise WorkspaceDeletionUnavailableError("Workspace deletion is unavailable.")

    scheduled_for = current_time + WORKSPACE_DELETION_GRACE_PERIOD

    workspace.registration_status = WorkspaceRegistrationStatus.DELETION_PENDING
    workspace.deletion_requested_at = current_time
    workspace.deletion_scheduled_for = scheduled_for

    SessionRepository(session).revoke_all_workspace_sessions(
        workspace_id=workspace.id,
        reason=SessionRevocationReason.WORKSPACE_DELETION_REQUESTED,
        now=current_time,
    )

    jobs = BackgroundJobRepository(session)
    jobs.cancel_queued_workspace_jobs(
        workspace_id=workspace.id,
        exclude_job_types=(BackgroundJobType.WORKSPACE_DELETION,),
        now=current_time,
    )
    # Any queued deletion job is stale here (the workspace was active), so
    # it is canceled rather than reused with an older schedule.
    jobs.cancel_queued_workspace_jobs(
        workspace_id=workspace.id,
        job_types=(BackgroundJobType.WORKSPACE_DELETION,),
        now=current_time,
    )
    jobs.enqueue(
        workspace_id=workspace.id,
        job_type=BackgroundJobType.WORKSPACE_DELETION,
        payload={},
        idempotency_key=workspace_deletion_idempotency_key(workspace.id),
        available_at=scheduled_for,
    )

    session.flush()

    return _pending_state(workspace, now=current_time)


def cancel_workspace_deletion(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    auth_session_id: int,
    current_password: str,
    now: datetime | None = None,
) -> None:
    """Restore an active workspace from a recovery session before cutoff.

    The caller commits. At or after ``scheduled_for`` cancellation loses
    permanently, even while the deletion job has not run.
    """

    current_time = now or datetime.now(UTC)
    workspace = _lock_workspace(session, workspace_id)
    administrator = _lock_administrator(session, user_id=user_id, workspace_id=workspace_id)

    if (
        workspace is None
        or administrator is None
        or not is_deletion_pending(workspace)
        or workspace.deletion_requested_at is None
        or workspace.deletion_scheduled_for is None
        or current_time >= workspace.deletion_scheduled_for
    ):
        raise WorkspaceDeletionUnavailableError("This workspace can no longer be restored.")

    recovery_session = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == auth_session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if (
        recovery_session is None
        or recovery_session.user_id != administrator.id
        or recovery_session.purpose is not AuthSessionPurpose.DELETION_RECOVERY
        or recovery_session.revoked_at is not None
        or recovery_session.expires_at <= current_time
        or recovery_session.created_at < workspace.deletion_requested_at
    ):
        raise WorkspaceDeletionUnavailableError("This workspace can no longer be restored.")

    if not verify_password(current_password, administrator.password_hash):
        raise WorkspaceDeletionPasswordError("The current password is incorrect.")

    BackgroundJobRepository(session).cancel_queued_workspace_jobs(
        workspace_id=workspace.id,
        job_types=(BackgroundJobType.WORKSPACE_DELETION,),
        now=current_time,
    )

    workspace.registration_status = WorkspaceRegistrationStatus.ACTIVE
    workspace.deletion_requested_at = None
    workspace.deletion_scheduled_for = None

    # The recovery session ends; it never becomes a standard session and
    # revoked sessions are not restored. LOGOUT is the closest existing
    # non-disclosed reason.
    SessionRepository(session).revoke_session(
        recovery_session,
        reason=SessionRevocationReason.LOGOUT,
        now=current_time,
    )

    session.flush()


def workspace_deletion_idempotency_key(workspace_id: int) -> str:
    return f"workspace_deletion:{workspace_id}"


def _lock_workspace(session: Session, workspace_id: int) -> Installation | None:
    return session.scalar(
        select(Installation)
        .where(Installation.id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _lock_administrator(
    session: Session,
    *,
    user_id: int,
    workspace_id: int,
) -> User | None:
    administrator = session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if not _is_workspace_administrator(administrator, workspace_id=workspace_id):
        return None

    return administrator


def _is_workspace_administrator(user: User | None, *, workspace_id: int) -> bool:
    return (
        user is not None
        and user.role is UserRole.ADMIN
        and user.workspace_id == workspace_id
        and user.is_active
        and user.email_verified_at is not None
    )


def _require_request_session(
    session: Session,
    *,
    auth_session_id: int,
    user_id: int,
    already_pending: bool,
    now: datetime,
) -> None:
    auth_session = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == auth_session_id)
        .execution_options(populate_existing=True)
    )

    if (
        auth_session is None
        or auth_session.user_id != user_id
        or auth_session.purpose is not AuthSessionPurpose.STANDARD
        or auth_session.expires_at <= now
    ):
        raise WorkspaceDeletionUnavailableError("Workspace deletion is unavailable.")

    if auth_session.revoked_at is None:
        return

    # A concurrent request that lost the workspace lock finds its session
    # revoked by the winner; it may only observe the existing schedule.
    if not (
        already_pending
        and auth_session.revocation_reason is SessionRevocationReason.WORKSPACE_DELETION_REQUESTED
    ):
        raise WorkspaceDeletionUnavailableError("Workspace deletion is unavailable.")


def _slug_matches(workspace: Installation, confirmation_slug: str) -> bool:
    if workspace.normalized_slug is None:
        return False

    try:
        normalized = normalize_workspace_slug(confirmation_slug)
    except InvalidWorkspaceSlugError:
        return False

    return normalized == workspace.normalized_slug


def _pending_state(workspace: Installation, *, now: datetime) -> WorkspaceDeletionState:
    if workspace.deletion_requested_at is None or workspace.deletion_scheduled_for is None:
        raise WorkspaceDeletionUnavailableError("Workspace deletion is unavailable.")

    return WorkspaceDeletionState(
        status=WorkspaceDeletionStatus.DELETION_PENDING,
        requested_at=workspace.deletion_requested_at,
        scheduled_for=workspace.deletion_scheduled_for,
        cancellable=now < workspace.deletion_scheduled_for,
    )
