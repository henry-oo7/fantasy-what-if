"""Onboarding outcome for terminal initial-workspace-sync job states.

Called only from the worker's existing job-success, terminal-failure and
expired-lease transactions, so the job status and the onboarding stage
commit together. It is deliberately limited to INITIAL_WORKSPACE_SYNC and
imports nothing from the worker, avoiding an import cycle.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobType,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation


def apply_initial_sync_outcome(
    session: Session,
    *,
    job: BackgroundJob,
    succeeded: bool,
) -> None:
    """Advance onboarding when an initial sync job reaches a terminal state.

    Success moves initial_sync_running to spectator_setup_pending; terminal
    failure moves it to initial_sync_failed. Any other workspace state is
    left untouched. The workspace stays provisional and is never activated.
    """

    if job.job_type is not BackgroundJobType.INITIAL_WORKSPACE_SYNC:
        return

    workspace = session.scalar(
        select(Installation).where(Installation.id == job.workspace_id).with_for_update()
    )

    if (
        workspace is None
        or workspace.registration_status is not WorkspaceRegistrationStatus.PROVISIONAL
        or workspace.onboarding_stage is not OnboardingStage.INITIAL_SYNC_RUNNING
    ):
        return

    workspace.onboarding_stage = (
        OnboardingStage.SPECTATOR_SETUP_PENDING
        if succeeded
        else OnboardingStage.INITIAL_SYNC_FAILED
    )
    session.flush()
