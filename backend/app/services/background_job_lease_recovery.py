"""Recover running background jobs whose worker lease expired."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_enums import BackgroundJobStatus
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from app.services.onboarding_sync_outcome import apply_initial_sync_outcome
from app.services.transactional_email_job_outcome import (
    apply_transactional_email_job_failure,
)

EXPIRED_LEASE_ERROR_CODE = "worker_lease_expired"
EXPIRED_LEASE_RETRY_DELAY = timedelta(seconds=30)
DEFAULT_RECOVERY_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class BackgroundJobLeaseRecoveryResult:
    """Summary of one committed lease-recovery scan."""

    inspected_count: int
    retry_scheduled_count: int
    failed_count: int

    @property
    def recovered_count(self) -> int:
        return self.retry_scheduled_count + self.failed_count


def recover_expired_background_jobs(
    session: Session,
    *,
    now: datetime | None = None,
    retry_delay: timedelta = (EXPIRED_LEASE_RETRY_DELAY),
    batch_size: int = DEFAULT_RECOVERY_BATCH_SIZE,
) -> BackgroundJobLeaseRecoveryResult:
    """Recover a bounded locked batch of expired jobs."""

    if retry_delay < timedelta(0):
        raise ValueError("retry_delay cannot be negative.")

    if batch_size < 1:
        raise ValueError("batch_size must be positive.")

    timestamp = now or datetime.now(UTC)
    retry_at = timestamp + retry_delay
    repository = BackgroundJobRepository(session)
    expired_jobs = repository.list_expired_running_jobs(
        now=timestamp,
        limit=batch_size,
    )

    retry_scheduled_count = 0
    failed_count = 0

    for job in expired_jobs:
        recovered_status = repository.recover_expired_lease(
            job,
            retry_at=retry_at,
            now=timestamp,
        )

        if recovered_status is BackgroundJobStatus.RETRY_SCHEDULED:
            retry_scheduled_count += 1
        elif recovered_status is BackgroundJobStatus.FAILED:
            failed_count += 1
            # Same transaction as the terminal job state.
            apply_initial_sync_outcome(session, job=job, succeeded=False)
            apply_transactional_email_job_failure(session, job=job)
        else:
            raise RuntimeError("Expired-lease recovery produced an unsupported job status.")

    return BackgroundJobLeaseRecoveryResult(
        inspected_count=len(expired_jobs),
        retry_scheduled_count=(retry_scheduled_count),
        failed_count=failed_count,
    )


def recover_expired_background_jobs_committed(
    *,
    session_factory: sessionmaker[Session],
    now: datetime | None = None,
    retry_delay: timedelta = (EXPIRED_LEASE_RETRY_DELAY),
    batch_size: int = DEFAULT_RECOVERY_BATCH_SIZE,
) -> BackgroundJobLeaseRecoveryResult:
    """Recover and commit one bounded expired-lease batch."""

    with session_factory() as session:
        result = recover_expired_background_jobs(
            session,
            now=now,
            retry_delay=retry_delay,
            batch_size=batch_size,
        )
        session.commit()

    return result
