"""Stable workspace lifecycle error responses."""

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import WorkspaceAccessError


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
