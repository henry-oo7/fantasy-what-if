from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    Season,
    SyncRun,
)


@dataclass(frozen=True, slots=True)
class StoredSyncStatus:
    league_id: int
    season: int
    last_synced_at: datetime | None
    latest_run: SyncRun | None


def get_sync_status(
    session: Session,
    *,
    espn_league_id: int,
    season_year: int,
) -> StoredSyncStatus | None:
    season = session.scalar(
        select(Season)
        .join(
            League,
            Season.league_id == League.id,
        )
        .where(
            League.espn_league_id == espn_league_id,
            Season.year == season_year,
        )
    )

    if season is None:
        return None

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
        league_id=espn_league_id,
        season=season.year,
        last_synced_at=season.last_synced_at,
        latest_run=latest_run,
    )
