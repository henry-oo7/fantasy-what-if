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


class PlayoffPredictorTeamResponse(BaseModel):
    """One current team record in the predictor."""

    team_id: int
    name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float


class PlayoffPredictorMatchupResponse(BaseModel):
    """One completed or remaining predictor matchup."""

    matchup_id: int
    week: int
    home_team_id: int
    away_team_id: int
    home_score: float | None
    away_score: float | None
    is_complete: bool


class PlayoffPredictorResponse(BaseModel):
    """Canonical input for an ephemeral playoff scenario."""

    league_id: int
    league_name: str
    season_id: int
    season: int
    current_week: int
    regular_season_weeks: int
    playoff_team_count: int
    playoff_seed_tie_rule: str
    playoff_seed_tie_rule_source: str
    allows_ties: bool
    teams: tuple[
        PlayoffPredictorTeamResponse,
        ...,
    ]
    completed_matchups: tuple[
        PlayoffPredictorMatchupResponse,
        ...,
    ]
    remaining_matchups: tuple[
        PlayoffPredictorMatchupResponse,
        ...,
    ]
