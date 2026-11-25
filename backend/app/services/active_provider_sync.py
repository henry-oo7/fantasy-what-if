"""Synchronize the active season through its configured provider."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.providers.base import ProviderAdapter
from app.providers.registry import ProviderRegistry
from app.services.provider_adapter_resolver import (
    resolve_active_provider_adapter,
)
from app.services.provider_sync import (
    ProviderSyncPlan,
    ProviderSyncResult,
    SyncJobAuthority,
    SyncWorkspaceRequirement,
    execute_provider_sync,
    prepare_provider_sync,
)
from app.services.season_lifecycle_reconciliation import (
    reconcile_stale_active_season,
)


def prepare_active_season_sync(
    *,
    session: Session,
    workspace_id: int,
    settings: Settings,
    full_sync: bool,
    registry: ProviderRegistry | None = None,
    job_authority: SyncJobAuthority | None = None,
) -> tuple[ProviderSyncPlan, ProviderAdapter]:
    """Preparation phase in the caller's transaction.

    Decrypts the workspace's stored credentials into the returned adapter
    (process memory only). The caller must commit and close the Session
    before passing the plan to :func:`execute_provider_sync`.
    """

    reconcile_stale_active_season(
        session,
        workspace_id=workspace_id,
    )

    context, provider = resolve_active_provider_adapter(
        session=session,
        workspace_id=workspace_id,
        settings=settings,
        registry=registry,
    )

    plan = prepare_provider_sync(
        session,
        season_id=context.season_id,
        provider=provider,
        full_sync=full_sync,
        requirement=SyncWorkspaceRequirement.ACTIVE_SEASON,
        job_authority=job_authority,
    )

    return plan, provider


def synchronize_active_season(
    *,
    session_factory: sessionmaker[Session],
    workspace_id: int,
    settings: Settings,
    full_sync: bool,
    registry: ProviderRegistry | None = None,
    job_authority: SyncJobAuthority | None = None,
) -> ProviderSyncResult:
    """Synchronize the active season through its resolved adapter."""

    with session_factory() as session:
        plan, provider = prepare_active_season_sync(
            session=session,
            workspace_id=workspace_id,
            settings=settings,
            full_sync=full_sync,
            registry=registry,
            job_authority=job_authority,
        )
        session.commit()

    return execute_provider_sync(
        session_factory=session_factory,
        plan=plan,
        provider=provider,
    )
