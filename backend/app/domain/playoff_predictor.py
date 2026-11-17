"""Read-only playoff predictor domain contracts."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlayoffPredictorTeam:
    """One team's current canonical record."""

    team_id: int
    name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float


@dataclass(frozen=True, slots=True)
class PlayoffPredictorMatchup:
    """One completed or remaining regular-season matchup."""

    matchup_id: int
    week: int
    home_team_id: int
    away_team_id: int
    home_score: float | None
    away_score: float | None
    is_complete: bool


@dataclass(frozen=True, slots=True)
class PlayoffPredictorSnapshot:
    """Canonical data required for an ephemeral playoff scenario."""

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
    teams: tuple[PlayoffPredictorTeam, ...]
    completed_matchups: tuple[
        PlayoffPredictorMatchup,
        ...,
    ]
    remaining_matchups: tuple[
        PlayoffPredictorMatchup,
        ...,
    ]
