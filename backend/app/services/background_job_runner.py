from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from threading import Event
from time import monotonic

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_enums import BackgroundJobType
from app.services.background_job_lease_recovery import (
    BackgroundJobLeaseRecoveryResult,
    recover_expired_background_jobs_committed,
)
from app.services.background_job_worker import (
    BackgroundJobExecutionResult,
    BackgroundJobHandler,
    execute_one_background_job,
)

LOGGER = logging.getLogger("fantasy_what_if.background_worker")


@dataclass(frozen=True, slots=True)
class BackgroundJobRunnerConfiguration:
    """Validated runtime controls for one worker process."""

    worker_id: str
    lease_duration: timedelta
    heartbeat_interval: timedelta
    recovery_interval: timedelta
    recovery_retry_delay: timedelta
    recovery_batch_size: int
    poll_seconds: float
    database_backoff_seconds: float

    def __post_init__(self) -> None:
        if not self.worker_id.strip():
            raise ValueError("worker_id cannot be empty.")

        if self.lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive.")

        if self.heartbeat_interval <= timedelta(0):
            raise ValueError("heartbeat_interval must be positive.")

        if self.heartbeat_interval >= self.lease_duration:
            raise ValueError("heartbeat_interval must be shorter than lease_duration.")

        if self.recovery_interval <= timedelta(0):
            raise ValueError("recovery_interval must be positive.")

        if self.recovery_retry_delay < timedelta(0):
            raise ValueError("recovery_retry_delay cannot be negative.")

        if self.recovery_batch_size < 1:
            raise ValueError("recovery_batch_size must be positive.")

        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive.")

        if self.database_backoff_seconds <= 0:
            raise ValueError("database_backoff_seconds must be positive.")


def run_background_job_worker(
    *,
    session_factory: sessionmaker[Session],
    configuration: BackgroundJobRunnerConfiguration,
    handlers: Mapping[
        BackgroundJobType,
        BackgroundJobHandler,
    ],
    shutdown_event: Event,
    execute_one: Callable[
        ...,
        BackgroundJobExecutionResult,
    ] = execute_one_background_job,
    recover_expired: Callable[
        ...,
        BackgroundJobLeaseRecoveryResult,
    ] = recover_expired_background_jobs_committed,
    monotonic_time: Callable[[], float] = monotonic,
) -> None:
    """Poll, recover, and execute jobs until shutdown."""

    LOGGER.info(
        "Background worker started: "
        "worker_id=%s, poll_seconds=%s, "
        "lease_seconds=%s, heartbeat_seconds=%s, "
        "recovery_seconds=%s.",
        configuration.worker_id,
        configuration.poll_seconds,
        configuration.lease_duration.total_seconds(),
        configuration.heartbeat_interval.total_seconds(),
        configuration.recovery_interval.total_seconds(),
    )

    next_recovery_at = 0.0

    while not shutdown_event.is_set():
        try:
            current_monotonic_time = monotonic_time()

            if current_monotonic_time >= next_recovery_at:
                recovery = recover_expired(
                    session_factory=session_factory,
                    retry_delay=(configuration.recovery_retry_delay),
                    batch_size=(configuration.recovery_batch_size),
                )
                next_recovery_at = (
                    current_monotonic_time + configuration.recovery_interval.total_seconds()
                )

                if recovery.recovered_count:
                    LOGGER.warning(
                        "Expired background-job leases recovered: retry_scheduled=%s, failed=%s.",
                        recovery.retry_scheduled_count,
                        recovery.failed_count,
                    )

            result = execute_one(
                session_factory=session_factory,
                worker_id=configuration.worker_id,
                lease_duration=(configuration.lease_duration),
                heartbeat_interval=(configuration.heartbeat_interval),
                handlers=handlers,
            )
        except SQLAlchemyError:
            LOGGER.exception(
                "Background worker database operation failed: worker_id=%s.",
                configuration.worker_id,
            )

            if shutdown_event.wait(timeout=(configuration.database_backoff_seconds)):
                break

            continue
        except Exception:
            LOGGER.exception(
                "Background worker iteration failed "
                "before a safe job outcome was persisted: "
                "worker_id=%s.",
                configuration.worker_id,
            )

            if shutdown_event.wait(timeout=(configuration.database_backoff_seconds)):
                break

            continue

        if result.claimed:
            LOGGER.info(
                "Background job iteration completed: "
                "worker_id=%s, job_id=%s, "
                "succeeded=%s, retry_scheduled=%s, "
                "failed=%s.",
                configuration.worker_id,
                result.job_id,
                result.succeeded,
                result.retry_scheduled,
                result.failed,
            )
            continue

        if shutdown_event.wait(timeout=configuration.poll_seconds):
            break

    LOGGER.info(
        "Background worker stopped: worker_id=%s.",
        configuration.worker_id,
    )
