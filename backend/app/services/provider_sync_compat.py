"""Compatibility boundary for canonical provider synchronization."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.db import model_registry  # noqa: F401
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
)
from app.db.platform_models import Installation
from app.providers.espn import (
    EspnProvider,
    credentials_from_values,
)
from app.services.espn_sync import SyncResult
from app.services.provider_sync import synchronize_provider


class ActiveSynchronizationContextError(RuntimeError):
    """The installation has no usable active synchronization context."""


def resolve_active_season_id(
    session: Session,
) -> int:
    """Resolve the single active installation's active season."""

    installations = session.scalars(
        select(Installation)
        .where(Installation.status == InstallationStatus.ACTIVE)
        .order_by(Installation.id)
    ).all()

    if not installations:
        raise ActiveSynchronizationContextError("No active installation is configured.")

    if len(installations) > 1:
        raise ActiveSynchronizationContextError("Multiple active installations are configured.")

    installation = installations[0]

    if installation.active_season_id is None:
        raise ActiveSynchronizationContextError(
            "The active installation does not have an active season."
        )

    return installation.active_season_id


def synchronize_active_provider(
    *,
    session: Session,
    settings: Settings,
    full_sync: bool,
) -> SyncResult:
    """Synchronize the active season and preserve the legacy result API."""

    season_id = resolve_active_season_id(session)

    credentials = credentials_from_values(
        league_id=settings.espn_league_id,
        season=settings.espn_season,
        espn_s2=settings.espn_s2.get_secret_value(),
        swid=settings.espn_swid.get_secret_value(),
    )

    provider = EspnProvider(credentials)

    if provider.provider is not FantasyProvider.ESPN:
        raise ActiveSynchronizationContextError("The configured provider is not ESPN.")

    result = synchronize_provider(
        session=session,
        season_id=season_id,
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
