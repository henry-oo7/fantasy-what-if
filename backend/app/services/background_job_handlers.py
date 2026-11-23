from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db.models import Season
from app.db.platform_enums import BackgroundJobType
from app.db.platform_models import Installation
from app.providers.registry import ProviderRegistry
from app.services.active_provider_sync import (
    synchronize_active_season,
)
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    BackgroundJobHandler,
    BackgroundJobSnapshot,
)
from app.services.onboarding_initial_sync import InitialWorkspaceSyncHandler
from app.services.synchronization_job_contract import (
    SYNCHRONIZATION_JOB_PAYLOAD_VERSION,
    synchronization_payload_contains_prohibited_names,
)


class SynchronizationBackgroundJobHandler:
    """Execute one workspace-owned season synchronization."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        full_sync: bool,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.full_sync = full_sync
        self.registry = registry

    def __call__(
        self,
        job: BackgroundJobSnapshot,
    ) -> None:
        season_id = validate_sync_job_payload(
            job,
            expected_full_sync=self.full_sync,
        )

        with self.session_factory() as session:
            validate_sync_job_ownership(
                session,
                workspace_id=job.workspace_id,
                season_id=season_id,
            )

            synchronize_active_season(
                session=session,
                workspace_id=job.workspace_id,
                settings=self.settings,
                full_sync=self.full_sync,
                registry=self.registry,
            )


def build_background_job_handlers(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    registry: ProviderRegistry | None = None,
) -> Mapping[
    BackgroundJobType,
    BackgroundJobHandler,
]:
    """Build the executable worker-handler registry."""

    return {
        (BackgroundJobType.INCREMENTAL_SEASON_SYNC): SynchronizationBackgroundJobHandler(
            session_factory=session_factory,
            settings=settings,
            full_sync=False,
            registry=registry,
        ),
        BackgroundJobType.FULL_SEASON_SYNC: (
            SynchronizationBackgroundJobHandler(
                session_factory=session_factory,
                settings=settings,
                full_sync=True,
                registry=registry,
            )
        ),
        BackgroundJobType.INITIAL_WORKSPACE_SYNC: InitialWorkspaceSyncHandler(
            session_factory=session_factory,
            settings=settings,
            registry=registry,
        ),
    }


def validate_sync_job_payload(
    job: BackgroundJobSnapshot,
    *,
    expected_full_sync: bool,
) -> int:
    if job.payload_version != SYNCHRONIZATION_JOB_PAYLOAD_VERSION:
        raise BackgroundJobExecutionError(
            safe_error_code=("unsupported_payload_version"),
            safe_error_message=("The background job payload version is not supported."),
        )

    payload = job.payload
    season_id = payload.get("season_id")

    if isinstance(season_id, bool) or not isinstance(season_id, int) or season_id < 1:
        raise BackgroundJobExecutionError(
            safe_error_code="invalid_job_payload",
            safe_error_message=("The background job payload is invalid."),
        )

    supplied_full_sync = payload.get(
        "full_sync",
        expected_full_sync,
    )

    if not isinstance(supplied_full_sync, bool) or supplied_full_sync is not expected_full_sync:
        raise BackgroundJobExecutionError(
            safe_error_code="invalid_job_payload",
            safe_error_message=("The background job synchronization mode is invalid."),
        )

    reject_secret_payload_fields(payload)

    return season_id


def reject_secret_payload_fields(
    payload: Mapping[str, Any],
) -> None:
    if synchronization_payload_contains_prohibited_names(dict(payload)):
        raise BackgroundJobExecutionError(
            safe_error_code="invalid_job_payload",
            safe_error_message=("The background job payload contains prohibited data."),
        )


def validate_sync_job_ownership(
    session: Session,
    *,
    workspace_id: int,
    season_id: int,
) -> None:
    workspace = session.get(
        Installation,
        workspace_id,
    )

    if workspace is None:
        raise BackgroundJobExecutionError(
            safe_error_code="workspace_unavailable",
            safe_error_message=("The background job workspace is unavailable."),
        )

    season = session.get(
        Season,
        season_id,
    )

    if season is None:
        raise BackgroundJobExecutionError(
            safe_error_code="season_unavailable",
            safe_error_message=("The background job season is unavailable."),
        )

    if season.league.installation_id != workspace_id:
        raise BackgroundJobExecutionError(
            safe_error_code=("workspace_ownership_mismatch"),
            safe_error_message=("The background job does not belong to the requested workspace."),
        )

    if workspace.active_season_id != season_id:
        raise BackgroundJobExecutionError(
            safe_error_code=("inactive_season_job"),
            safe_error_message=("The background job season is not the workspace's active season."),
        )
