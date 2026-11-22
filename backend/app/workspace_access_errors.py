"""Stable workspace lifecycle and session error responses."""

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import SessionReplacedError, WorkspaceAccessError


async def workspace_access_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Return a stable lifecycle error without exposing workspace state."""

    del request

    if not isinstance(
        exception,
        WorkspaceAccessError,
    ):
        raise TypeError("Expected WorkspaceAccessError.")

    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "detail": exception.detail,
            "code": exception.code,
            "retryable": False,
        },
    )


async def session_replaced_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Return the stable replaced-session error without device details."""

    del request

    if not isinstance(
        exception,
        SessionReplacedError,
    ):
        raise TypeError("Expected SessionReplacedError.")

    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={
            "detail": exception.detail,
            "code": exception.code,
            "retryable": False,
        },
    )
