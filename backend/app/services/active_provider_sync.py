"""Synchronize the active season through its configured provider."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.providers.registry import ProviderRegistry
from app.services.provider_adapter_resolver import (
    resolve_active_provider_adapter,
)
from app.services.provider_sync import (
    ProviderSyncResult,
    synchronize_provider,
)
from app.services.season_lifecycle_reconciliation import (
    reconcile_stale_active_season,
)


def synchronize_active_season(
    *,
    session: Session,
    workspace_id: int,
    settings: Settings,
    full_sync: bool,
    registry: ProviderRegistry | None = None,
) -> ProviderSyncResult:
    """Synchronize the active season through its resolved adapter."""

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

    return synchronize_provider(
        session=session,
        season_id=context.season_id,
        provider=provider,
        full_sync=full_sync,
    )
