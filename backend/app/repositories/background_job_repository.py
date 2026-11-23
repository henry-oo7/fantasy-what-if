from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.db.platform_models import BackgroundJob

ACTIVE_JOB_STATUSES = (
    BackgroundJobStatus.PENDING,
    BackgroundJobStatus.RUNNING,
    BackgroundJobStatus.RETRY_SCHEDULED,
)

ACTIVE_IDEMPOTENCY_CONSTRAINT = "uq_background_jobs_active_idempotency_key"

QUEUED_JOB_STATUSES = (
    BackgroundJobStatus.PENDING,
    BackgroundJobStatus.RETRY_SCHEDULED,
)


class BackgroundJobRepositoryError(RuntimeError):
    """Base error for durable background-job operations."""


class BackgroundJobClaimError(BackgroundJobRepositoryError):
    """A worker could not claim a job safely."""


@dataclass(frozen=True, slots=True)
class EnqueueBackgroundJobResult:
    """Result of an idempotent enqueue operation."""

    job: BackgroundJob
    existing: bool


class BackgroundJobRepository:
    """Persist and atomically claim workspace-owned jobs."""

    def __init__(
        self,
        session: Session,
    ) -> None:
        self.session = session

    def enqueue(
        self,
        *,
        workspace_id: int,
        job_type: BackgroundJobType,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
        payload_version: int = 1,
        priority: int = 0,
        maximum_attempts: int = 5,
        available_at: datetime | None = None,
    ) -> EnqueueBackgroundJobResult:
        if workspace_id < 1:
            raise ValueError("workspace_id must be positive.")

        if payload_version < 1:
            raise ValueError("payload_version must be positive.")

        if maximum_attempts < 1:
            raise ValueError("maximum_attempts must be positive.")

        normalized_key = normalize_idempotency_key(idempotency_key)
        timestamp = available_at or datetime.now(UTC)

        if normalized_key is not None:
            existing = self.get_active_by_idempotency_key(normalized_key)

            if existing is not None:
                return EnqueueBackgroundJobResult(
                    job=existing,
                    existing=True,
                )

        job = BackgroundJob(
            workspace_id=workspace_id,
            job_type=job_type,
            status=BackgroundJobStatus.PENDING,
            priority=priority,
            idempotency_key=normalized_key,
            payload_version=payload_version,
            payload=dict(payload),
            progress=None,
            attempt_count=0,
            maximum_attempts=maximum_attempts,
            available_at=timestamp,
        )

        try:
            with self.session.begin_nested():
                self.session.add(job)
                self.session.flush()
        except IntegrityError as exc:
            if normalized_key is None or _constraint_name(exc) != ACTIVE_IDEMPOTENCY_CONSTRAINT:
                raise

            existing = self.get_active_by_idempotency_key(normalized_key)

            if existing is None:
                raise BackgroundJobRepositoryError(
                    "The active idempotent job could not be resolved after a queue conflict."
                ) from exc

            return EnqueueBackgroundJobResult(
                job=existing,
                existing=True,
            )

        return EnqueueBackgroundJobResult(
            job=job,
            existing=False,
        )

    def get_active_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> BackgroundJob | None:
        normalized_key = normalize_idempotency_key(idempotency_key)

        if normalized_key is None:
            return None

        return self.session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.idempotency_key == normalized_key,
                BackgroundJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(BackgroundJob.id)
            .limit(1)
        )

    def get_for_workspace(
        self,
        *,
        job_id: int,
        workspace_id: int,
    ) -> BackgroundJob | None:
        if job_id < 1:
            raise ValueError("job_id must be positive.")

        if workspace_id < 1:
            raise ValueError("workspace_id must be positive.")

        return self.session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == job_id,
                BackgroundJob.workspace_id == workspace_id,
            )
        )

    def claim_next_available(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> BackgroundJob | None:
        normalized_worker_id = normalize_worker_id(worker_id)

        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive.")

        timestamp = now or datetime.now(UTC)

        job = self.session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.status.in_(
                    (
                        BackgroundJobStatus.PENDING,
                        BackgroundJobStatus.RETRY_SCHEDULED,
                    )
                ),
                BackgroundJob.available_at <= timestamp,
                BackgroundJob.attempt_count < BackgroundJob.maximum_attempts,
            )
            .order_by(
                BackgroundJob.priority.desc(),
                BackgroundJob.available_at,
                BackgroundJob.id,
            )
            .with_for_update(
                skip_locked=True,
            )
            .limit(1)
        )

        if job is None:
            return None

        if job.status not in (
            BackgroundJobStatus.PENDING,
            BackgroundJobStatus.RETRY_SCHEDULED,
        ):
            raise BackgroundJobClaimError("Only pending or retry-scheduled jobs may be claimed.")

        job.status = BackgroundJobStatus.RUNNING
        job.claimed_at = timestamp
        job.lease_expires_at = timestamp + lease_duration
        job.claimed_by = normalized_worker_id
        job.started_at = timestamp
        job.attempt_count += 1
        job.completed_at = None
        job.failed_at = None
        job.canceled_at = None
        job.safe_error_code = None
        job.safe_error_message = None
        job.retryable = None

        self.session.flush()

        return job

    def list_expired_running_jobs(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> tuple[BackgroundJob, ...]:
        if limit < 1:
            raise ValueError("limit must be positive.")

        timestamp = now or datetime.now(UTC)

        jobs = self.session.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.status == BackgroundJobStatus.RUNNING,
                BackgroundJob.lease_expires_at < timestamp,
            )
            .order_by(
                BackgroundJob.lease_expires_at,
                BackgroundJob.id,
            )
            .with_for_update(
                skip_locked=True,
            )
            .limit(limit)
        ).all()

        return tuple(jobs)

    def recover_expired_lease(
        self,
        job: BackgroundJob,
        *,
        retry_at: datetime,
        now: datetime | None = None,
    ) -> BackgroundJobStatus:
        """Recover one locked running job with an expired lease."""

        timestamp = now or datetime.now(UTC)

        if job.status is not BackgroundJobStatus.RUNNING:
            raise BackgroundJobRepositoryError(
                "Only a running background job may have its expired lease recovered."
            )

        if job.lease_expires_at is None:
            raise BackgroundJobRepositoryError(
                "The running background job does not have a lease expiration."
            )

        if job.lease_expires_at >= timestamp:
            raise BackgroundJobRepositoryError("The background job lease has not expired.")

        safe_error_code = "worker_lease_expired"
        safe_error_message = "The background worker stopped before the job completed."

        job.claimed_at = None
        job.lease_expires_at = None
        job.claimed_by = None
        job.started_at = None
        job.completed_at = None
        job.canceled_at = None
        job.safe_error_code = safe_error_code
        job.safe_error_message = safe_error_message

        if job.attempt_count < job.maximum_attempts:
            job.status = BackgroundJobStatus.RETRY_SCHEDULED
            job.available_at = retry_at
            job.failed_at = None
            job.retryable = True
        else:
            job.status = BackgroundJobStatus.FAILED
            job.failed_at = timestamp
            job.retryable = False

        self.session.flush()

        return job.status

    def renew_lease(
        self,
        job: BackgroundJob,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> None:
        self._require_running_worker(
            job,
            worker_id=worker_id,
        )

        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive.")

        timestamp = now or datetime.now(UTC)

        job.lease_expires_at = timestamp + lease_duration
        self.session.flush()

    def update_progress(
        self,
        job: BackgroundJob,
        *,
        worker_id: str,
        progress: dict[str, Any],
    ) -> None:
        self._require_running_worker(
            job,
            worker_id=worker_id,
        )

        job.progress = dict(progress)
        self.session.flush()

    def mark_succeeded(
        self,
        job: BackgroundJob,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> None:
        self._require_running_worker(
            job,
            worker_id=worker_id,
        )

        timestamp = now or datetime.now(UTC)

        job.status = BackgroundJobStatus.SUCCEEDED
        job.completed_at = timestamp
        job.failed_at = None
        job.canceled_at = None
        job.lease_expires_at = None
        job.safe_error_code = None
        job.safe_error_message = None
        job.retryable = None

        self.session.flush()

    def schedule_retry(
        self,
        job: BackgroundJob,
        *,
        worker_id: str,
        available_at: datetime,
        safe_error_code: str,
        safe_error_message: str,
    ) -> None:
        self._require_running_worker(
            job,
            worker_id=worker_id,
        )

        if job.attempt_count >= job.maximum_attempts:
            raise BackgroundJobRepositoryError(
                "The background job has exhausted its automatic attempts."
            )

        job.status = BackgroundJobStatus.RETRY_SCHEDULED
        job.available_at = available_at
        job.safe_error_code = normalize_safe_error_code(safe_error_code)
        job.safe_error_message = normalize_safe_error_message(safe_error_message)
        job.retryable = True
        job.claimed_at = None
        job.lease_expires_at = None
        job.claimed_by = None
        job.started_at = None
        job.completed_at = None
        job.failed_at = None
        job.canceled_at = None

        self.session.flush()

    def defer_claimed_job(
        self,
        job: BackgroundJob,
        *,
        worker_id: str,
        available_at: datetime,
        safe_error_code: str,
        safe_error_message: str,
    ) -> None:
        """Return a claimed, not-yet-due job to ``pending``.

        The handler did no work, so the attempt consumed by the claim is
        restored; repeated early claims never exhaust the job.
        """

        self._require_running_worker(
            job,
            worker_id=worker_id,
        )

        if job.attempt_count < 1:
            raise BackgroundJobRepositoryError("A claimed background job must have an attempt.")

        job.status = BackgroundJobStatus.PENDING
        job.available_at = available_at
        job.attempt_count -= 1
        job.safe_error_code = normalize_safe_error_code(safe_error_code)
        job.safe_error_message = normalize_safe_error_message(safe_error_message)
        job.retryable = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.claimed_by = None
        job.started_at = None
        job.completed_at = None
        job.failed_at = None
        job.canceled_at = None

        self.session.flush()

    def mark_failed(
        self,
        job: BackgroundJob,
        *,
        worker_id: str,
        safe_error_code: str,
        safe_error_message: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> None:
        self._require_running_worker(
            job,
            worker_id=worker_id,
        )

        timestamp = now or datetime.now(UTC)

        job.status = BackgroundJobStatus.FAILED
        job.failed_at = timestamp
        job.completed_at = None
        job.canceled_at = None
        job.lease_expires_at = None
        job.safe_error_code = normalize_safe_error_code(safe_error_code)
        job.safe_error_message = normalize_safe_error_message(safe_error_message)
        job.retryable = retryable

        self.session.flush()

    def cancel_before_execution(
        self,
        job: BackgroundJob,
        *,
        now: datetime | None = None,
    ) -> None:
        if job.status not in (
            BackgroundJobStatus.PENDING,
            BackgroundJobStatus.RETRY_SCHEDULED,
        ):
            raise BackgroundJobRepositoryError(
                "Only pending or retry-scheduled jobs may be canceled before execution."
            )

        timestamp = now or datetime.now(UTC)

        job.status = BackgroundJobStatus.CANCELED
        job.canceled_at = timestamp
        job.completed_at = None
        job.failed_at = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.claimed_by = None
        job.started_at = None
        job.retryable = None

        self.session.flush()

    def cancel_queued_workspace_jobs(
        self,
        *,
        workspace_id: int,
        job_types: tuple[BackgroundJobType, ...] | None = None,
        exclude_job_types: tuple[BackgroundJobType, ...] = (),
        now: datetime | None = None,
    ) -> int:
        """Cancel one workspace's pending and retry-scheduled jobs.

        Running jobs are never rewritten here: their handlers recheck the
        workspace lifecycle under lock before committing. The caller must
        hold the workspace row lock so no job can be claimed or enqueued
        for the workspace in a way that escapes this statement.
        """

        if workspace_id < 1:
            raise ValueError("workspace_id must be positive.")

        timestamp = now or datetime.now(UTC)
        statement = update(BackgroundJob).where(
            BackgroundJob.workspace_id == workspace_id,
            BackgroundJob.status.in_(QUEUED_JOB_STATUSES),
        )

        if job_types is not None:
            statement = statement.where(BackgroundJob.job_type.in_(job_types))

        if exclude_job_types:
            statement = statement.where(BackgroundJob.job_type.not_in(exclude_job_types))

        result = self.session.execute(
            statement.values(
                status=BackgroundJobStatus.CANCELED,
                canceled_at=timestamp,
                completed_at=None,
                failed_at=None,
                claimed_at=None,
                lease_expires_at=None,
                claimed_by=None,
                started_at=None,
                retryable=None,
            ).execution_options(synchronize_session="fetch")
        )

        return int(result.rowcount or 0)

    def _require_running_worker(
        self,
        job: BackgroundJob,
        *,
        worker_id: str,
    ) -> str:
        normalized_worker_id = normalize_worker_id(worker_id)

        if job.status is not BackgroundJobStatus.RUNNING:
            raise BackgroundJobRepositoryError("The background job is not running.")

        if job.claimed_by != normalized_worker_id:
            raise BackgroundJobRepositoryError("The background job belongs to another worker.")

        return normalized_worker_id


def normalize_idempotency_key(
    idempotency_key: str | None,
) -> str | None:
    if idempotency_key is None:
        return None

    normalized = idempotency_key.strip()

    if not normalized:
        raise ValueError("idempotency_key cannot be empty.")

    if len(normalized) > 255:
        raise ValueError("idempotency_key cannot exceed 255 characters.")

    return normalized


def normalize_worker_id(
    worker_id: str,
) -> str:
    normalized = worker_id.strip()

    if not normalized:
        raise ValueError("worker_id cannot be empty.")

    if len(normalized) > 255:
        raise ValueError("worker_id cannot exceed 255 characters.")

    return normalized


def normalize_safe_error_code(
    safe_error_code: str,
) -> str:
    normalized = safe_error_code.strip()

    if not normalized:
        raise ValueError("safe_error_code cannot be empty.")

    if len(normalized) > 100:
        raise ValueError("safe_error_code cannot exceed 100 characters.")

    return normalized


def normalize_safe_error_message(
    safe_error_message: str,
) -> str:
    normalized = " ".join(safe_error_message.strip().split())

    if not normalized:
        raise ValueError("safe_error_message cannot be empty.")

    return normalized


def _constraint_name(
    error: IntegrityError,
) -> str | None:
    diagnostic = getattr(
        error.orig,
        "diag",
        None,
    )

    return getattr(
        diagnostic,
        "constraint_name",
        None,
    )
