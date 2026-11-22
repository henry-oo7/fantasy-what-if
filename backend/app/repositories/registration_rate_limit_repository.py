"""Persistent privacy-preserving registration action limits."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.auth.security import hmac_identifier
from app.db.auth_models import RegistrationRateLimit


@dataclass(frozen=True, slots=True)
class RegistrationRateLimitResult:
    """Decision after recording one public action request."""

    allowed: bool
    request_count: int
    retry_after_seconds: int


class RegistrationRateLimitRepository:
    """Count public registration actions in fixed windows."""

    def __init__(
        self,
        session: Session,
        *,
        secret: str,
    ) -> None:
        normalized_secret = secret.strip()

        if not normalized_secret:
            raise ValueError("Registration rate-limit secret cannot be empty.")

        self.session = session
        self.secret = normalized_secret

    def record_request(
        self,
        *,
        action: str,
        identity: str,
        maximum_requests: int,
        window_minutes: int,
        now: datetime | None = None,
    ) -> RegistrationRateLimitResult:
        """Record one request and return its fixed-window decision."""

        normalized_action = _normalize_action(action)

        if maximum_requests < 1:
            raise ValueError("Maximum registration requests must be positive.")

        if window_minutes < 1:
            raise ValueError("Registration rate-limit window must be positive.")

        timestamp = now or datetime.now(UTC)
        window = timedelta(minutes=window_minutes)
        identity_hash = hmac_identifier(
            identity,
            secret=self.secret,
            namespace=f"registration-rate-limit:{normalized_action}",
        )

        self._acquire_lock(
            action=normalized_action,
            identity_hash=identity_hash,
        )

        stored = self.session.scalar(
            select(RegistrationRateLimit).where(
                RegistrationRateLimit.action == normalized_action,
                RegistrationRateLimit.identity_hash == identity_hash,
            )
        )

        if stored is None or stored.window_started_at + window <= timestamp:
            if stored is None:
                stored = RegistrationRateLimit(
                    action=normalized_action,
                    identity_hash=identity_hash,
                    request_count=1,
                    window_started_at=timestamp,
                    last_request_at=timestamp,
                )
                self.session.add(stored)
            else:
                stored.request_count = 1
                stored.window_started_at = timestamp
                stored.last_request_at = timestamp

            self.session.flush()

            return RegistrationRateLimitResult(
                allowed=True,
                request_count=1,
                retry_after_seconds=0,
            )

        stored.request_count += 1
        stored.last_request_at = timestamp

        allowed = stored.request_count <= maximum_requests
        retry_after_seconds = 0

        if not allowed:
            retry_after_seconds = max(
                int((stored.window_started_at + window - timestamp).total_seconds()),
                1,
            )

        self.session.flush()

        return RegistrationRateLimitResult(
            allowed=allowed,
            request_count=stored.request_count,
            retry_after_seconds=retry_after_seconds,
        )

    def delete_expired(
        self,
        *,
        retention_minutes: int,
        now: datetime | None = None,
    ) -> int:
        """Delete counters older than the configured retention."""

        if retention_minutes < 1:
            raise ValueError("Registration rate-limit retention must be positive.")

        timestamp = now or datetime.now(UTC)
        cutoff = timestamp - timedelta(
            minutes=retention_minutes,
        )

        result = self.session.execute(
            delete(RegistrationRateLimit)
            .where(RegistrationRateLimit.last_request_at < cutoff)
            .execution_options(
                synchronize_session="fetch",
            )
        )

        self.session.flush()

        return int(result.rowcount or 0)

    def _acquire_lock(
        self,
        *,
        action: str,
        identity_hash: str,
    ) -> None:
        lock_material = f"{action}:{identity_hash}"
        lock_key = int(
            hmac_identifier(
                lock_material,
                secret=self.secret,
                namespace="registration-rate-limit-lock",
            )[:16],
            16,
        )

        if lock_key >= 2**63:
            lock_key -= 2**64

        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {
                "lock_key": lock_key,
            },
        )


def _normalize_action(
    action: str,
) -> str:
    normalized = action.strip().casefold().replace("-", "_")

    if not normalized:
        raise ValueError("Registration rate-limit action cannot be empty.")

    if len(normalized) > 50:
        raise ValueError("Registration rate-limit action cannot exceed 50 characters.")

    if not all(
        character.isascii() and (character.islower() or character.isdigit() or character == "_")
        for character in normalized
    ):
        raise ValueError("Registration rate-limit action contains unsupported characters.")

    return normalized
