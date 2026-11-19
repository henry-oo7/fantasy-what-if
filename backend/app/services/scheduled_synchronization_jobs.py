"""Create durable scheduled synchronization jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import Installation
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)

SCHEDULED_SYNC_PRIORITY = 25
SCHEDULED_SYNC_MAXIMUM_ATTEMPTS = 5
SCHEDULED_SYNC_PAYLOAD_VERSION = 1


@dataclass(frozen=True, slots=True)
class ScheduledSynchronizationTarget:
    """One workspace and season eligible for scheduling."""

    workspace_id: int
    season_id: int


@dataclass(frozen=True, slots=True)
class ScheduledSynchronizationJob:
    """Safe result from one scheduled enqueue operation."""

    job_id: int
    workspace_id: int
    season_id: int
    status: BackgroundJobStatus
    existing: bool


@dataclass(frozen=True, slots=True)
class ScheduledSynchronizationBatch:
    """Summary of one committed scheduler dispatch window."""

    scheduled_for: datetime
    jobs: tuple[ScheduledSynchronizationJob, ...]

    @property
    def created_count(self) -> int:
        return sum(not job.existing for job in self.jobs)

    @property
    def existing_count(self) -> int:
        return sum(job.existing for job in self.jobs)


def list_scheduled_synchronization_targets(
    session: Session,
) -> tuple[ScheduledSynchronizationTarget, ...]:
    """List active workspaces with valid active seasons."""

    rows = session.execute(
        select(
            Installation.id,
            Season.id,
        )
        .join(
            Season,
            Season.id == Installation.active_season_id,
        )
        .join(
            League,
            League.id == Season.league_id,
        )
        .where(
            Installation.status == InstallationStatus.ACTIVE,
            Installation.active_league_id == League.id,
            League.installation_id == Installation.id,
            Season.status == SeasonStatus.ACTIVE,
        )
        .order_by(
            Installation.id,
        )
    ).all()

    return tuple(
        ScheduledSynchronizationTarget(
            workspace_id=workspace_id,
            season_id=season_id,
        )
        for workspace_id, season_id in rows
    )


def schedule_window_key(
    scheduled_for: datetime,
) -> str:
    """Create a stable UTC key for one configured run window."""

    if scheduled_for.tzinfo is None:
        raise ValueError("scheduled_for must be timezone-aware.")

    normalized = scheduled_for.astimezone(UTC).replace(
        second=0,
        microsecond=0,
    )

    return normalized.strftime("%Y%m%dT%H%MZ")


def enqueue_scheduled_synchronization(
    session: Session,
    *,
    target: ScheduledSynchronizationTarget,
    scheduled_for: datetime,
) -> ScheduledSynchronizationJob:
    """Create or reuse one scheduled incremental job."""

    window_key = schedule_window_key(scheduled_for)
    idempotency_key = f"scheduled-sync:{target.workspace_id}:{target.season_id}:{window_key}"

    result = BackgroundJobRepository(session).enqueue(
        workspace_id=target.workspace_id,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        payload={
            "season_id": target.season_id,
            "full_sync": False,
            "trigger": "scheduled",
        },
        idempotency_key=idempotency_key,
        payload_version=(SCHEDULED_SYNC_PAYLOAD_VERSION),
        priority=SCHEDULED_SYNC_PRIORITY,
        maximum_attempts=(SCHEDULED_SYNC_MAXIMUM_ATTEMPTS),
    )

    job_id = result.job.id

    if job_id is None:
        raise RuntimeError(
            "The scheduled synchronization job does not have a persisted identifier."
        )

    return ScheduledSynchronizationJob(
        job_id=job_id,
        workspace_id=target.workspace_id,
        season_id=target.season_id,
        status=result.job.status,
        existing=result.existing,
    )


def enqueue_scheduled_synchronization_batch(
    session: Session,
    *,
    scheduled_for: datetime,
) -> ScheduledSynchronizationBatch:
    """Enqueue one incremental job per eligible workspace."""

    targets = list_scheduled_synchronization_targets(session)
    jobs = tuple(
        enqueue_scheduled_synchronization(
            session,
            target=target,
            scheduled_for=scheduled_for,
        )
        for target in targets
    )

    return ScheduledSynchronizationBatch(
        scheduled_for=scheduled_for,
        jobs=jobs,
    )
