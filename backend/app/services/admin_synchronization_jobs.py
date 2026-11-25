"""Create durable administrator-triggered synchronization jobs."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.platform_enums import BackgroundJobStatus
from app.services.active_season_context import (
    resolve_active_season_context,
)
from app.services.season_sync_jobs import enqueue_season_sync

ADMIN_SYNC_PRIORITY = 100


@dataclass(frozen=True, slots=True)
class AdministratorSynchronizationJob:
    """Safe result returned after accepting synchronization."""

    job_id: int
    status: BackgroundJobStatus
    existing: bool


def enqueue_administrator_synchronization(
    session: Session,
    *,
    workspace_id: int,
    full_sync: bool,
) -> AdministratorSynchronizationJob:
    """Create or reuse the active Season's one synchronization job.

    Transitional adapter: the workspace resolves its active Season, then the
    Season-owned job is enqueued. Every Season sync is full (ADR 0010 payloads
    carry no mode), so ``full_sync`` is accepted for API compatibility only.
    """

    del full_sync

    context = resolve_active_season_context(
        session=session,
        workspace_id=workspace_id,
    )

    result = enqueue_season_sync(
        session,
        season_id=context.season_id,
        priority=ADMIN_SYNC_PRIORITY,
    )

    job_id = result.job.id

    if job_id is None:
        raise RuntimeError("The accepted synchronization job does not have a persisted identifier.")

    return AdministratorSynchronizationJob(
        job_id=job_id,
        status=result.job.status,
        existing=result.existing,
    )
