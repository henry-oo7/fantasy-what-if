"""Create durable administrator-triggered synchronization jobs."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from app.services.active_season_context import (
    resolve_active_season_context,
)
from app.services.synchronization_job_contract import (
    SYNCHRONIZATION_JOB_PAYLOAD_VERSION,
    build_synchronization_job_payload,
)

ADMIN_SYNC_PRIORITY = 100
ADMIN_SYNC_MAXIMUM_ATTEMPTS = 5


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
    """Create or reuse one active administrator sync job."""

    context = resolve_active_season_context(
        session=session,
        workspace_id=workspace_id,
    )

    job_type = (
        BackgroundJobType.FULL_SEASON_SYNC
        if full_sync
        else BackgroundJobType.INCREMENTAL_SEASON_SYNC
    )
    synchronization_mode = "full" if full_sync else "incremental"
    idempotency_key = f"admin-sync:{workspace_id}:{context.season_id}:{synchronization_mode}"

    result = BackgroundJobRepository(session).enqueue(
        workspace_id=workspace_id,
        job_type=job_type,
        payload=build_synchronization_job_payload(
            season_id=context.season_id,
            full_sync=full_sync,
            trigger="administrator",
        ),
        idempotency_key=idempotency_key,
        payload_version=SYNCHRONIZATION_JOB_PAYLOAD_VERSION,
        priority=ADMIN_SYNC_PRIORITY,
        maximum_attempts=(ADMIN_SYNC_MAXIMUM_ATTEMPTS),
    )

    job_id = result.job.id

    if job_id is None:
        raise RuntimeError("The accepted synchronization job does not have a persisted identifier.")

    return AdministratorSynchronizationJob(
        job_id=job_id,
        status=result.job.status,
        existing=result.existing,
    )
