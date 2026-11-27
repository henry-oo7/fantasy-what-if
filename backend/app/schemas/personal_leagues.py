"""Personal League and Season responses (ADR 0010).

``season_id`` is a locator for later reads, never authorization. Responses
never include user, workspace, claim, provider-connection, access-row or job
IDs, failure codes, or whose credentials synchronize the Season.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.platform_enums import FantasyProvider, SeasonSyncState

ReadableAccessStatus = Literal["active", "reverification_required"]


class PersonalSeasonResponse(BaseModel):
    season_id: int
    season_year: int
    access_status: ReadableAccessStatus
    # NULL only for a legacy Season never described by the sync lifecycle.
    sync_state: SeasonSyncState | None
    data_available: bool
    last_successful_sync_at: datetime | None


class PersonalLeagueResponse(BaseModel):
    provider: FantasyProvider
    provider_league_id: str
    league_name: str
    seasons: list[PersonalSeasonResponse]


class PersonalLeaguesResponse(BaseModel):
    # Set only while the stored default is still readable.
    default_season_id: int | None
    leagues: list[PersonalLeagueResponse]


class PersonalSeasonDetailResponse(PersonalSeasonResponse):
    provider: FantasyProvider
    provider_league_id: str
    league_name: str


class DefaultSeasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season_id: int = Field(gt=0)


class DefaultSeasonResponse(BaseModel):
    default_season_id: int
