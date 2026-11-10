"""League and season inventory response schemas."""

from datetime import datetime

from pydantic import BaseModel


class ImportedSeasonResponse(BaseModel):
    """One season already imported into Fantasy What-If."""

    season_id: int
    league_id: int
    league_name: str
    year: int
    status: str | None
    is_active: bool
    last_synced_at: datetime | None


class ImportedSeasonsResponse(BaseModel):
    """Imported seasons available for historical browsing."""

    seasons: tuple[ImportedSeasonResponse, ...]


class ImportSeasonRequest(BaseModel):
    """Administrator request to import another season."""

    year: int


class ImportSeasonResponse(BaseModel):
    """Safe result of an imported provider season."""

    season_id: int
    league_id: int
    year: int
    status: str
    synchronization_enabled: bool
    teams_upserted: int
    matchups_upserted: int
    fantasy_byes_upserted: int
    player_week_scores_upserted: int
