"""Centralized browser security headers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.core.settings import Settings

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

PERMISSIONS_POLICY = (
    "accelerometer=(), "
    "ambient-light-sensor=(), "
    "autoplay=(), "
    "camera=(), "
    "display-capture=(), "
    "encrypted-media=(), "
    "fullscreen=(), "
    "geolocation=(), "
    "gyroscope=(), "
    "magnetometer=(), "
    "microphone=(), "
    "midi=(), "
    "payment=(), "
    "picture-in-picture=(), "
    "publickey-credentials-get=(), "
    "screen-wake-lock=(), "
    "serial=(), "
    "usb=(), "
    "web-share=(), "
    "xr-spatial-tracking=()"
)

HSTS_VALUE = "max-age=31536000; includeSubDomains"

NO_STORE_PREFIXES = (
    "/api/auth",
    "/api/setup",
    "/api/admin",
)


def requires_no_store(path: str) -> bool:
    """Return whether a response may contain sensitive state."""

    return any(path == prefix or path.startswith(prefix + "/") for prefix in NO_STORE_PREFIXES)


def apply_security_headers(
    response: Response,
    *,
    request_path: str,
    settings: Settings,
) -> None:
    """Apply browser security controls to one response."""

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = PERMISSIONS_POLICY

    if requires_no_store(request_path):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    if settings.is_production:
        response.headers["Strict-Transport-Security"] = HSTS_VALUE


def security_headers_middleware(
    settings: Settings,
) -> Callable[
    [
        Request,
        Callable[
            [Request],
            Awaitable[Response],
        ],
    ],
    Awaitable[Response],
]:
    """Build middleware using validated settings."""

    async def middleware(
        request: Request,
        call_next: Callable[
            [Request],
            Awaitable[Response],
        ],
    ) -> Response:
        response = await call_next(request)

        apply_security_headers(
            response,
            request_path=request.url.path,
            settings=settings,
        )

        return response

    return middleware
