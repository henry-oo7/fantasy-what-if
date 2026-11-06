from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TeamSnapshot:
    team_id: int
    name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float


@dataclass(frozen=True, slots=True)
class MatchupSnapshot:
    week: int
    home_team_id: int
    away_team_id: int
    home_score: float
    away_score: float


@dataclass(frozen=True, slots=True)
class LeagueSnapshot:
    league_id: int
    name: str
    season: int
    current_week: int
    regular_season_weeks: int
    teams: tuple[TeamSnapshot, ...]
    matchups: tuple[MatchupSnapshot, ...]
