from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    Season,
    SyncRun,
)
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import ProviderConnection


@dataclass(frozen=True, slots=True)
class StoredSyncStatus:
    """Stored synchronization status for one canonical season."""

    provider: FantasyProvider
    season_id: int
    provider_league_id: str
    provider_season_id: str
    last_synced_at: datetime | None
    latest_run: SyncRun | None


def get_sync_status(
    session: Session,
    *,
    season_id: int,
) -> StoredSyncStatus | None:
    """Return synchronization status for one internal season."""

    if season_id < 1:
        raise ValueError("season_id must be positive.")

    row = session.execute(
        select(
            Season,
            League,
            ProviderConnection,
        )
        .join(
            League,
            Season.league_id == League.id,
        )
        .join(
            ProviderConnection,
            Season.provider_connection_id == ProviderConnection.id,
        )
        .where(Season.id == season_id)
    ).one_or_none()

    if row is None:
        return None

    season, league, provider_connection = row

    provider_league_id = league.provider_league_id
    provider_season_id = season.provider_season_id

    if provider_league_id is None:
        raise RuntimeError("The synchronized league does not have a canonical provider ID.")

    if provider_season_id is None:
        raise RuntimeError("The synchronized season does not have a canonical provider ID.")

    if league.provider is not provider_connection.provider:
        raise RuntimeError("The synchronized league and provider connection do not match.")

    latest_run = session.scalar(
        select(SyncRun)
        .where(SyncRun.season_id == season.id)
        .order_by(
            SyncRun.started_at.desc(),
            SyncRun.id.desc(),
        )
        .limit(1)
    )

    return StoredSyncStatus(
        provider=provider_connection.provider,
        season_id=season.id,
        provider_league_id=provider_league_id,
        provider_season_id=provider_season_id,
        last_synced_at=season.last_synced_at,
        latest_run=latest_run,
    )
