"""Production-safe API exception handling."""

from __future__ import annotations

import logging

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

GENERIC_SERVER_ERROR = "An unexpected server error occurred."


async def validation_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Return validation locations without submitted values."""

    if not isinstance(
        exception,
        RequestValidationError,
    ):
        raise TypeError("Expected RequestValidationError.")

    sanitized_errors = []

    for error in exception.errors():
        sanitized_error = {
            "type": error.get(
                "type",
                "value_error",
            ),
            "loc": list(
                error.get(
                    "loc",
                    (),
                )
            ),
            "msg": error.get(
                "msg",
                "Invalid request value.",
            ),
        }

        sanitized_errors.append(sanitized_error)

    logger.info(
        "Request validation failed: method=%s path=%s fields=%s",
        request.method,
        request.url.path,
        [error["loc"] for error in sanitized_errors],
    )

    return JSONResponse(
        status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
        content={
            "detail": sanitized_errors,
        },
    )


async def unexpected_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Log unexpected failures and return a generic response."""

    logger.exception(
        "Unhandled API exception: method=%s path=%s",
        request.method,
        request.url.path,
        exc_info=exception,
    )

    return JSONResponse(
        status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
        content={
            "detail": GENERIC_SERVER_ERROR,
        },
    )
