from datetime import datetime

from pydantic import BaseModel


class SyncRunResponse(BaseModel):
    id: int
    status: str
    is_full_sync: bool
    started_at: datetime
    completed_at: datetime | None
    requested_from_week: int | None
    requested_through_week: int | None
    teams_upserted: int
    matchups_upserted: int
    player_week_scores_upserted: int
    safe_error_message: str | None


class SyncStatusResponse(BaseModel):
    league_id: int
    season: int
    last_synced_at: datetime | None
    latest_run: SyncRunResponse | None


class SyncRequest(BaseModel):
    full_sync: bool = False


class SyncResultResponse(BaseModel):
    league_id: int
    season: int
    current_week: int
    full_sync: bool
    player_weeks: tuple[int, ...]
    teams_upserted: int
    matchups_upserted: int
    fantasy_byes_upserted: int
    player_week_scores_upserted: int
