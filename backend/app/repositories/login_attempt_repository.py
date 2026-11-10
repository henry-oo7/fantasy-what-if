"""Persistent login-attempt throttling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.auth.security import hmac_identifier
from app.db.auth_models import LoginAttempt


@dataclass(frozen=True, slots=True)
class LoginThrottleState:
    """Current throttle decision for one login key."""

    failed_attempts: int
    locked_until: datetime | None
    retry_after_seconds: int

    @property
    def is_locked(self) -> bool:
        return self.retry_after_seconds > 0


class LoginAttemptRepository:
    """Manage privacy-preserving login throttle rows."""

    def __init__(
        self,
        session: Session,
        *,
        secret: str,
        maximum_attempts: int = 3,
        attempt_window_minutes: int = 15,
        lockout_minutes: int = 15,
        delay_schedule_seconds: tuple[
            tuple[int, int],
            ...,
        ]
        | None = None,
    ) -> None:
        if maximum_attempts < 1:
            raise ValueError("Maximum login attempts must be positive.")

        if attempt_window_minutes < 1:
            raise ValueError("Login attempt window must be positive.")

        if lockout_minutes < 1:
            raise ValueError("Login lockout duration must be positive.")

        self.session = session
        self.secret = secret
        self.maximum_attempts = maximum_attempts
        self.attempt_window = timedelta(
            minutes=attempt_window_minutes,
        )
        self.lockout_duration = timedelta(
            minutes=lockout_minutes,
        )
        self.delay_schedule_seconds = (
            tuple(
                sorted(
                    delay_schedule_seconds,
                    key=lambda item: item[0],
                )
            )
            if delay_schedule_seconds is not None
            else (
                (
                    maximum_attempts,
                    lockout_minutes * 60,
                ),
            )
        )

        for threshold, seconds in self.delay_schedule_seconds:
            if threshold < 1:
                raise ValueError("Login delay thresholds must be positive.")

            if seconds < 1:
                raise ValueError("Login delay durations must be positive.")

    def throttle_state(
        self,
        *,
        username: str,
        source_address: str | None,
        now: datetime | None = None,
    ) -> LoginThrottleState:
        username_hash, source_hash = self._key(
            username=username,
            source_address=source_address,
        )

        return self.throttle_state_for_bucket(
            bucket_username=username_hash,
            bucket_source=source_hash,
            now=now,
        )

    def throttle_state_for_bucket(
        self,
        *,
        bucket_username: str,
        bucket_source: str,
        now: datetime | None = None,
    ) -> LoginThrottleState:
        """Read throttle state for a precomputed private bucket."""

        timestamp = now or datetime.now(UTC)
        username_hash, source_hash = self._validate_bucket(
            bucket_username=bucket_username,
            bucket_source=bucket_source,
        )

        self._acquire_lock(
            username_hash=username_hash,
            source_hash=source_hash,
        )

        attempt = self._load(
            username_hash=username_hash,
            source_hash=source_hash,
        )

        if attempt is None:
            return LoginThrottleState(
                failed_attempts=0,
                locked_until=None,
                retry_after_seconds=0,
            )

        if attempt.locked_until is not None and attempt.locked_until > timestamp:
            return self._state(
                attempt,
                now=timestamp,
            )

        if attempt.last_failed_at < timestamp - self.attempt_window:
            self.session.delete(attempt)
            self.session.flush()

            return LoginThrottleState(
                failed_attempts=0,
                locked_until=None,
                retry_after_seconds=0,
            )

        if attempt.locked_until is not None:
            attempt.locked_until = None
            attempt.failed_attempts = 0
            attempt.first_failed_at = timestamp
            attempt.last_failed_at = timestamp
            self.session.flush()

            return LoginThrottleState(
                failed_attempts=0,
                locked_until=None,
                retry_after_seconds=0,
            )

        return self._state(
            attempt,
            now=timestamp,
        )

    def record_failure(
        self,
        *,
        username: str,
        source_address: str | None,
        now: datetime | None = None,
    ) -> LoginThrottleState:
        username_hash, source_hash = self._key(
            username=username,
            source_address=source_address,
        )

        return self.record_failure_for_bucket(
            bucket_username=username_hash,
            bucket_source=source_hash,
            now=now,
        )

    def record_failure_for_bucket(
        self,
        *,
        bucket_username: str,
        bucket_source: str,
        now: datetime | None = None,
    ) -> LoginThrottleState:
        """Record a failure for a precomputed private bucket."""

        timestamp = now or datetime.now(UTC)
        username_hash, source_hash = self._validate_bucket(
            bucket_username=bucket_username,
            bucket_source=bucket_source,
        )

        self._acquire_lock(
            username_hash=username_hash,
            source_hash=source_hash,
        )

        attempt = self._load(
            username_hash=username_hash,
            source_hash=source_hash,
        )

        if attempt is None:
            attempt = LoginAttempt(
                username_hash=username_hash,
                source_hash=source_hash,
                failed_attempts=1,
                first_failed_at=timestamp,
                last_failed_at=timestamp,
                locked_until=None,
            )
            self.session.add(attempt)
        elif attempt.last_failed_at < timestamp - self.attempt_window:
            attempt.failed_attempts = 1
            attempt.first_failed_at = timestamp
            attempt.last_failed_at = timestamp
            attempt.locked_until = None
        else:
            attempt.failed_attempts += 1
            attempt.last_failed_at = timestamp

        delay_seconds = self._delay_for_failure(attempt.failed_attempts)

        if delay_seconds > 0:
            attempt.locked_until = timestamp + timedelta(
                seconds=delay_seconds,
            )
        else:
            attempt.locked_until = None

        self.session.flush()

        return self._state(
            attempt,
            now=timestamp,
        )

    def clear(
        self,
        *,
        username: str,
        source_address: str | None,
    ) -> None:
        username_hash, source_hash = self._key(
            username=username,
            source_address=source_address,
        )

        self.clear_bucket(
            bucket_username=username_hash,
            bucket_source=source_hash,
        )

    def clear_bucket(
        self,
        *,
        bucket_username: str,
        bucket_source: str,
    ) -> None:
        """Clear one precomputed private throttle bucket."""

        username_hash, source_hash = self._validate_bucket(
            bucket_username=bucket_username,
            bucket_source=bucket_source,
        )

        self._acquire_lock(
            username_hash=username_hash,
            source_hash=source_hash,
        )

        self.session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.username_hash == username_hash,
                LoginAttempt.source_hash == source_hash,
            )
        )
        self.session.flush()

    def delete_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        timestamp = now or datetime.now(UTC)
        cutoff = timestamp - self.attempt_window

        result = self.session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.last_failed_at < cutoff,
                (LoginAttempt.locked_until.is_(None) | (LoginAttempt.locked_until <= timestamp)),
            )
        )

        return int(result.rowcount or 0)

    @staticmethod
    def _validate_bucket(
        *,
        bucket_username: str,
        bucket_source: str,
    ) -> tuple[str, str]:
        normalized_username = bucket_username.strip()
        normalized_source = bucket_source.strip()

        if not normalized_username:
            raise ValueError("Login bucket username cannot be empty.")

        if not normalized_source:
            raise ValueError("Login bucket source cannot be empty.")

        return (
            normalized_username,
            normalized_source,
        )

    def _delay_for_failure(
        self,
        failed_attempts: int,
    ) -> int:
        delay = 0

        for threshold, seconds in self.delay_schedule_seconds:
            if failed_attempts >= threshold:
                delay = seconds

        return delay

    def _key(
        self,
        *,
        username: str,
        source_address: str | None,
    ) -> tuple[str, str]:
        username_hash = hmac_identifier(
            username,
            secret=self.secret,
            namespace="login-username",
        )
        source_hash = hmac_identifier(
            source_address or "unknown",
            secret=self.secret,
            namespace="login-source",
        )

        return username_hash, source_hash

    def _load(
        self,
        *,
        username_hash: str,
        source_hash: str,
    ) -> LoginAttempt | None:
        return self.session.scalar(
            select(LoginAttempt).where(
                LoginAttempt.username_hash == username_hash,
                LoginAttempt.source_hash == source_hash,
            )
        )

    def _acquire_lock(
        self,
        *,
        username_hash: str,
        source_hash: str,
    ) -> None:
        lock_material = f"{username_hash}:{source_hash}"
        lock_key = int(
            hmac_identifier(
                lock_material,
                secret=self.secret,
                namespace="login-lock",
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

    @staticmethod
    def _state(
        attempt: LoginAttempt,
        *,
        now: datetime,
    ) -> LoginThrottleState:
        retry_after = 0

        if attempt.locked_until is not None and attempt.locked_until > now:
            retry_after = max(
                int((attempt.locked_until - now).total_seconds()),
                1,
            )

        return LoginThrottleState(
            failed_attempts=attempt.failed_attempts,
            locked_until=attempt.locked_until,
            retry_after_seconds=retry_after,
        )
