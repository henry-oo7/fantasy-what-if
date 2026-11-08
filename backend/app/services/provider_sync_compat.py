"""Compatibility boundary for canonical provider synchronization."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.services.espn_sync import SyncResult
from app.services.provider_adapter_resolver import (
    ProviderAdapterResolutionError,
    resolve_active_provider_adapter,
)
from app.services.provider_sync import synchronize_provider


class ActiveSynchronizationContextError(ProviderAdapterResolutionError):
    """Compatibility alias for active-context failures."""


def synchronize_active_provider(
    *,
    session: Session,
    settings: Settings,
    full_sync: bool,
) -> SyncResult:
    """Synchronize the active season and preserve the legacy result API."""

    context, provider = resolve_active_provider_adapter(
        session=session,
        settings=settings,
    )

    result = synchronize_provider(
        session=session,
        season_id=context.season_id,
        provider=provider,
        full_sync=full_sync,
    )

    return SyncResult(
        league_id=int(result.provider_league_id),
        season=int(result.provider_season_id),
        current_week=result.current_week,
        full_sync=result.full_sync,
        player_weeks=result.player_weeks,
        teams_upserted=result.teams_upserted,
        matchups_upserted=result.matchups_upserted,
        fantasy_byes_upserted=(result.fantasy_byes_upserted),
        player_week_scores_upserted=(result.player_week_scores_upserted),
    )
