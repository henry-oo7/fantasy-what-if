"""Persistent throttle for destructive personal actions (per user and per client address)."""

from fastapi import status
from fastapi.responses import JSONResponse

from app.core.settings import Settings
from app.db.session import SessionFactory
from app.repositories.registration_rate_limit_repository import RegistrationRateLimitRepository


def record_destructive_attempt(
    settings: Settings,
    *,
    action: str,
    user_id: int,
    source: str | None,
    maximum_requests: int,
    window_minutes: int,
    code: str,
) -> JSONResponse | None:
    """Count one attempt per user and per address in a short committed transaction.

    Returns the 429 response when either bucket is full. With no resolvable client
    address only the per-user bucket applies. Identities are hashed by the repository.
    """

    identities = [("user", f"user:{user_id}")]

    if source is not None:
        identities.append(("source", source))

    with SessionFactory() as session:
        limiter = RegistrationRateLimitRepository(
            session, secret=settings.app_secret_key.get_secret_value()
        )
        decisions = [
            limiter.record_request(
                action=f"{action}_{scope}",
                identity=identity,
                maximum_requests=maximum_requests,
                window_minutes=window_minutes,
            )
            for scope, identity in identities
        ]
        session.commit()

    blocked = [decision for decision in decisions if not decision.allowed]

    if not blocked:
        return None

    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": str(max(decision.retry_after_seconds for decision in blocked))},
        content={"detail": "Too many attempts. Try again later.", "code": code, "retryable": True},
    )
