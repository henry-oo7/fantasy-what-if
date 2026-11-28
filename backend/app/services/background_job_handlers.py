from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db.models import Season
from app.db.platform_enums import BackgroundJobType
from app.db.platform_models import Installation
from app.notifications.recipient_encryption import TransactionalEmailRecipientCipher
from app.providers.espn.league_access import HttpGet
from app.providers.registry import ProviderRegistry
from app.repositories.provider_league_claim_repository import claimed_workspace_id
from app.services.access_reverification import reverify_access
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    BackgroundJobHandler,
    BackgroundJobSnapshot,
)
from app.services.onboarding_initial_sync import InitialWorkspaceSyncHandler
from app.services.orphan_league_purge import OrphanLeaguePurgeJobHandler
from app.services.provider_sync import SyncJobAuthority, synchronize_season
from app.services.season_sync_jobs import enqueue_season_sync
from app.services.synchronization_job_contract import (
    SYNCHRONIZATION_JOB_PAYLOAD_VERSION,
    InvalidAccessReverificationPayloadError,
    InvalidSeasonSyncPayloadError,
    synchronization_payload_contains_prohibited_names,
    validate_access_reverification_payload,
    validate_season_sync_payload,
)
from app.services.transactional_email_delivery import TransactionalEmailDeliveryHandler
from app.services.workspace_deletion_execution import WorkspaceDeletionJobHandler


class SeasonSynchronizationJobHandler:
    """Execute one League-Season synchronization (ADR 0010).

    The Season comes only from the exact payload; workspace active-Season
    state never selects what is synchronized.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.registry = registry

    def __call__(
        self,
        job: BackgroundJobSnapshot,
    ) -> None:
        try:
            season_id = validate_season_sync_payload(
                job.payload, payload_version=job.payload_version
            )
        except InvalidSeasonSyncPayloadError as exc:
            raise BackgroundJobExecutionError(
                safe_error_code="invalid_job_payload",
                safe_error_message="The background job payload is invalid.",
            ) from exc

        synchronize_season(
            session_factory=self.session_factory,
            season_id=season_id,
            settings=self.settings,
            registry=self.registry,
            job_authority=SyncJobAuthority(job_id=job.id, attempt_count=job.attempt_count),
        )


class AccessReverificationJobHandler:
    """Reverify one user's access to one Season (ADR 0010).

    The user and Season come only from the exact payload. Provider proof runs
    through :func:`reverify_access` with no transaction open.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        http_get: HttpGet | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.http_get = http_get

    def __call__(
        self,
        job: BackgroundJobSnapshot,
    ) -> None:
        try:
            user_id, season_id = validate_access_reverification_payload(
                job.payload, payload_version=job.payload_version
            )
        except InvalidAccessReverificationPayloadError as exc:
            raise BackgroundJobExecutionError(
                safe_error_code="invalid_job_payload",
                safe_error_message="The background job payload is invalid.",
            ) from exc

        reverify_access(
            session_factory=self.session_factory,
            user_id=user_id,
            season_id=season_id,
            settings=self.settings,
            job_authority=SyncJobAuthority(job_id=job.id, attempt_count=job.attempt_count),
            http_get=self.http_get,
        )


class LegacySynchronizationJobAdapter:
    """Compatibility for persisted workspace incremental/full sync jobs.

    Shortcut: re-enqueues the payload Season through the Season-owned job and
    succeeds; retire once no workspace-era sync job can still be queued. It
    never synchronizes a Season other than the payload's own.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        full_sync: bool,
    ) -> None:
        self.session_factory = session_factory
        self.full_sync = full_sync

    def __call__(
        self,
        job: BackgroundJobSnapshot,
    ) -> None:
        season_id = validate_sync_job_payload(
            job,
            expected_full_sync=self.full_sync,
        )

        if job.workspace_id is None:
            raise BackgroundJobExecutionError(
                safe_error_code="workspace_unavailable",
                safe_error_message="The background job workspace is unavailable.",
            )

        with self.session_factory() as session:
            validate_sync_job_ownership(
                session,
                workspace_id=job.workspace_id,
                season_id=season_id,
            )
            enqueue_season_sync(session, season_id=season_id)
            session.commit()


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
        BackgroundJobType.LEAGUE_SEASON_SYNC: SeasonSynchronizationJobHandler(
            session_factory=session_factory,
            settings=settings,
            registry=registry,
        ),
        BackgroundJobType.ACCESS_REVERIFICATION: AccessReverificationJobHandler(
            session_factory=session_factory,
            settings=settings,
        ),
        BackgroundJobType.ORPHAN_LEAGUE_PURGE: OrphanLeaguePurgeJobHandler(
            session_factory=session_factory,
        ),
        BackgroundJobType.INCREMENTAL_SEASON_SYNC: LegacySynchronizationJobAdapter(
            session_factory=session_factory,
            full_sync=False,
        ),
        BackgroundJobType.FULL_SEASON_SYNC: LegacySynchronizationJobAdapter(
            session_factory=session_factory,
            full_sync=True,
        ),
        BackgroundJobType.INITIAL_WORKSPACE_SYNC: InitialWorkspaceSyncHandler(
            session_factory=session_factory,
            settings=settings,
            registry=registry,
        ),
        BackgroundJobType.WORKSPACE_DELETION: WorkspaceDeletionJobHandler(
            session_factory=session_factory,
            recipient_cipher_factory=lambda: TransactionalEmailRecipientCipher(
                settings.transactional_email_keyring()
            ),
        ),
        BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY: TransactionalEmailDeliveryHandler(
            session_factory=session_factory,
            settings=settings,
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

    if claimed_workspace_id(session, season.league) != workspace_id:
        raise BackgroundJobExecutionError(
            safe_error_code=("workspace_ownership_mismatch"),
            safe_error_message=("The background job does not belong to the requested workspace."),
        )

    if workspace.active_season_id != season_id:
        raise BackgroundJobExecutionError(
            safe_error_code=("inactive_season_job"),
            safe_error_message=("The background job season is not the workspace's active season."),
        )
