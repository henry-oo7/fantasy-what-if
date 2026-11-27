"""Personal League import boundary (ADR 0010).

The client supplies only the provider target and, optionally, its own
private credentials. Internal IDs, ownership, access state, proof mode and
job identifiers are never accepted: unknown fields are rejected.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.platform_enums import SeasonSyncState
from app.schemas.onboarding import (
    ESPN_LEAGUE_ID_MAX_LENGTH,
    ESPN_LEAGUE_ID_PATTERN,
    MAXIMUM_SEASON_YEAR,
    MINIMUM_SEASON_YEAR,
    EspnPrivateCredentials,
    normalize_espn_league_id,
    reject_future_season_year,
)

ESPN_PROVIDER = "espn"


class LeagueImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    # Provider-specific rules apply only to a supported provider, so an
    # unsupported provider always gets the stable unsupported result.
    provider_league_id: str = Field(min_length=1, max_length=64)
    season_year: int = Field(ge=MINIMUM_SEASON_YEAR, le=MAXIMUM_SEASON_YEAR)
    credentials: EspnPrivateCredentials | None = None

    _reject_future_season = field_validator("season_year")(reject_future_season_year)

    @model_validator(mode="after")
    def normalize_espn_target(self) -> LeagueImportRequest:
        if self.provider == ESPN_PROVIDER:
            if len(self.provider_league_id) > ESPN_LEAGUE_ID_MAX_LENGTH or not re.fullmatch(
                ESPN_LEAGUE_ID_PATTERN, self.provider_league_id
            ):
                raise ValueError("ESPN League IDs contain digits only.")

            self.provider_league_id = normalize_espn_league_id(self.provider_league_id)

        return self


class LeagueImportResponse(BaseModel):
    """Safe import outcome.

    ``season_id`` is a locator for later navigation, never authorization.
    Never includes user, workspace, connection, access-row or job IDs,
    credential state, or whether anyone else imported the League.
    """

    provider: Literal["espn"]
    provider_league_id: str
    league_name: str
    season_id: int
    season_year: int
    access_status: Literal["active"]
    sync_state: SeasonSyncState
    data_available: bool
    sync_queued: bool
