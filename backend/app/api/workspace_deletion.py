"""FR-20 workspace deletion routes.

These routes are the only administrator routes reachable by a
``deletion_recovery`` session, so this router deliberately has no
router-level active-workspace dependency. Each route authorizes the
session purpose itself through the workspace deletion service:

- status: standard session on an active workspace, or recovery session
  on a deletion-pending workspace;
- request: standard session only (the default-deny dependency);
- cancellation: recovery session only.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import (
    AuthenticationContext,
    require_any_purpose_session_csrf,
    require_session_csrf,
    resolve_session_authentication_context,
)
from app.api.security import validate_request_origin
from app.auth.cookies import clear_authentication_cookies
from app.core.settings import Settings, get_settings
from app.db.auth_models import AuthSessionPurpose, UserRole
from app.db.session import SessionFactory
from app.repositories.registration_rate_limit_repository import (
    RegistrationRateLimitRepository,
)
from app.schemas.workspace_deletion import (
    WorkspaceDeletionCanceledResponse,
    WorkspaceDeletionCancellationRequest,
    WorkspaceDeletionRequest,
    WorkspaceDeletionRequestedResponse,
    WorkspaceDeletionStatusResponse,
)
from app.services.workspace_deletion import (
    WORKSPACE_DELETION_ATTEMPT_WINDOW_MINUTES,
    WORKSPACE_DELETION_CANCEL_ACTION,
    WORKSPACE_DELETION_GRACE_PERIOD_DAYS,
    WORKSPACE_DELETION_MAXIMUM_ATTEMPTS,
    WORKSPACE_DELETION_REQUEST_ACTION,
    WorkspaceDeletionError,
    WorkspaceDeletionPasswordError,
    WorkspaceDeletionSlugMismatchError,
    WorkspaceDeletionStatus,
    WorkspaceDeletionUnavailableError,
    cancel_workspace_deletion,
    rate_limit_identity,
    read_workspace_deletion_state,
    request_workspace_deletion,
)

router = APIRouter(
    prefix="/api/admin/workspace/deletion",
    tags=["workspace deletion"],
)

_UNAVAILABLE_DETAIL = "Workspace deletion is unavailable."
_NOT_RESTORABLE_DETAIL = "This workspace can no longer be restored."


def _require_administrator(context: AuthenticationContext) -> None:
    if context.role is not UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required.",
        )


def _error_response(
    error: WorkspaceDeletionError,
    *,
    unavailable_detail: str,
) -> JSONResponse:
    if isinstance(error, WorkspaceDeletionPasswordError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": "The current password is incorrect.",
                "code": error.code,
                "retryable": True,
            },
        )

    if isinstance(error, WorkspaceDeletionSlugMismatchError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": "Type the workspace name exactly as shown to confirm.",
                "code": error.code,
                "retryable": True,
            },
        )

    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": unavailable_detail,
            "code": WorkspaceDeletionUnavailableError.code,
            "retryable": False,
        },
    )


def _record_attempt(
    *,
    settings: Settings,
    action: str,
    context: AuthenticationContext,
) -> JSONResponse | None:
    """Count one attempt in its own transaction, before password checks."""

    with SessionFactory() as session:
        limit = RegistrationRateLimitRepository(
            session,
            secret=settings.app_secret_key.get_secret_value(),
        ).record_request(
            action=action,
            identity=rate_limit_identity(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
            ),
            maximum_requests=WORKSPACE_DELETION_MAXIMUM_ATTEMPTS,
            window_minutes=WORKSPACE_DELETION_ATTEMPT_WINDOW_MINUTES,
        )
        session.commit()

    if limit.allowed:
        return None

    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": str(limit.retry_after_seconds)},
        content={
            "detail": "Too many attempts. Try again later.",
            "code": "workspace_deletion_throttled",
            "retryable": True,
        },
    )


@router.get("", response_model=WorkspaceDeletionStatusResponse)
def read_workspace_deletion_status(
    context: Annotated[
        AuthenticationContext,
        Depends(resolve_session_authentication_context),
    ],
) -> WorkspaceDeletionStatusResponse | JSONResponse:
    _require_administrator(context)

    try:
        with SessionFactory() as session:
            state = read_workspace_deletion_state(
                session,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                session_purpose=context.session_purpose,
            )
    except WorkspaceDeletionError as exc:
        return _error_response(exc, unavailable_detail=_UNAVAILABLE_DETAIL)

    if state.status is WorkspaceDeletionStatus.ACTIVE:
        return WorkspaceDeletionStatusResponse(
            status=state.status,
            grace_period_days=WORKSPACE_DELETION_GRACE_PERIOD_DAYS,
            confirmation_slug=state.confirmation_slug,
        )

    return WorkspaceDeletionStatusResponse(
        status=state.status,
        requested_at=state.requested_at,
        scheduled_for=state.scheduled_for,
        cancellable=state.cancellable,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkspaceDeletionRequestedResponse,
)
def request_deletion(
    payload: WorkspaceDeletionRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        AuthenticationContext,
        Depends(require_session_csrf),
    ],
    _: Annotated[None, Depends(validate_request_origin)],
) -> JSONResponse:
    """Request deletion from a standard administrator session."""

    _require_administrator(context)

    throttled = _record_attempt(
        settings=settings,
        action=WORKSPACE_DELETION_REQUEST_ACTION,
        context=context,
    )

    if throttled is not None:
        return throttled

    try:
        with SessionFactory() as session:
            state = request_workspace_deletion(
                session,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                auth_session_id=context.session_id,
                current_password=payload.current_password,
                confirmation_slug=payload.confirmation_slug,
            )
            session.commit()
    except WorkspaceDeletionError as exc:
        return _error_response(exc, unavailable_detail=_UNAVAILABLE_DETAIL)

    if state.scheduled_for is None:
        raise RuntimeError("A pending deletion must have a scheduled time.")

    response = JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=WorkspaceDeletionRequestedResponse(
            detail=(
                "Workspace deletion was requested. Sign in again before the "
                "scheduled time to cancel it."
            ),
            scheduled_for=state.scheduled_for,
        ).model_dump(mode="json"),
    )
    clear_authentication_cookies(response, settings=settings)

    return response


@router.post(
    "/cancellation",
    response_model=WorkspaceDeletionCanceledResponse,
)
def cancel_deletion(
    payload: WorkspaceDeletionCancellationRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        AuthenticationContext,
        Depends(require_any_purpose_session_csrf),
    ],
    _: Annotated[None, Depends(validate_request_origin)],
) -> JSONResponse:
    """Cancel deletion from a deletion-recovery session before cutoff."""

    _require_administrator(context)

    if context.session_purpose is not AuthSessionPurpose.DELETION_RECOVERY:
        return _error_response(
            WorkspaceDeletionUnavailableError(_NOT_RESTORABLE_DETAIL),
            unavailable_detail=_NOT_RESTORABLE_DETAIL,
        )

    throttled = _record_attempt(
        settings=settings,
        action=WORKSPACE_DELETION_CANCEL_ACTION,
        context=context,
    )

    if throttled is not None:
        return throttled

    try:
        with SessionFactory() as session:
            cancel_workspace_deletion(
                session,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                auth_session_id=context.session_id,
                current_password=payload.current_password,
            )
            session.commit()
    except WorkspaceDeletionError as exc:
        return _error_response(exc, unavailable_detail=_NOT_RESTORABLE_DETAIL)

    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content=WorkspaceDeletionCanceledResponse(
            detail="Deletion canceled. Sign in again to continue.",
        ).model_dump(mode="json"),
    )
    clear_authentication_cookies(response, settings=settings)

    return response
