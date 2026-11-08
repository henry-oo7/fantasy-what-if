"""Compatibility boundary for canonical provider synchronization."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.services.provider_adapter_resolver import (
    ProviderAdapterResolutionError,
    resolve_active_provider_adapter,
)
from app.services.provider_sync import (
    ProviderSyncResult,
    synchronize_provider,
)


class ActiveSynchronizationContextError(ProviderAdapterResolutionError):
    """Compatibility alias for active-context failures."""


def synchronize_active_provider(
    *,
    session: Session,
    settings: Settings,
    full_sync: bool,
) -> ProviderSyncResult:
    """Synchronize the active season and preserve the legacy result API."""

    context, provider = resolve_active_provider_adapter(
        session=session,
        settings=settings,
    )

    return synchronize_provider(
        session=session,
        season_id=context.season_id,
        provider=provider,
        full_sync=full_sync,
    )
