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
