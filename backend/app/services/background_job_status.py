"""Workspace-scoped, safe background-job status reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)


@dataclass(frozen=True, slots=True)
class BackgroundJobStatusSnapshot:
    """Safe job state suitable for authenticated clients."""

    job_id: int
    job_type: BackgroundJobType
    status: BackgroundJobStatus
    attempt_count: int
    maximum_attempts: int
    progress: dict[str, Any] | None
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    canceled_at: datetime | None
    safe_error_code: str | None
    safe_error_message: str | None
    retryable: bool | None


def get_background_job_status(
    session: Session,
    *,
    job_id: int,
    workspace_id: int,
) -> BackgroundJobStatusSnapshot | None:
    """Return safe job state only when workspace ownership matches."""

    job = BackgroundJobRepository(session).get_for_workspace(
        job_id=job_id,
        workspace_id=workspace_id,
    )

    if job is None:
        return None

    return BackgroundJobStatusSnapshot(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        attempt_count=job.attempt_count,
        maximum_attempts=job.maximum_attempts,
        progress=(dict(job.progress) if job.progress is not None else None),
        available_at=job.available_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failed_at=job.failed_at,
        canceled_at=job.canceled_at,
        safe_error_code=job.safe_error_code,
        safe_error_message=job.safe_error_message,
        retryable=job.retryable,
    )
