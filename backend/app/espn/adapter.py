from typing import Protocol

from app.domain.league import LeagueSnapshot, MatchupSnapshot, TeamSnapshot


class EspnTeam(Protocol):
    team_id: int
    team_name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float


class EspnMatchup(Protocol):
    home_team: EspnTeam
    away_team: EspnTeam
    home_score: float
    away_score: float


class EspnSettings(Protocol):
    name: str
    reg_season_count: int


class EspnLeague(Protocol):
    league_id: int
    year: int
    current_week: int
    settings: EspnSettings
    teams: list[EspnTeam]

    def box_scores(self, week: int) -> list[EspnMatchup]: ...


def create_team_snapshot(team: EspnTeam) -> TeamSnapshot:
    return TeamSnapshot(
        team_id=team.team_id,
        name=team.team_name,
        wins=team.wins,
        losses=team.losses,
        ties=team.ties,
        points_for=team.points_for,
        points_against=team.points_against,
    )


def create_matchup_snapshot(
    matchup: EspnMatchup,
    *,
    week: int,
) -> MatchupSnapshot:
    return MatchupSnapshot(
        week=week,
        home_team_id=matchup.home_team.team_id,
        away_team_id=matchup.away_team.team_id,
        home_score=matchup.home_score,
        away_score=matchup.away_score,
    )


def create_league_snapshot(league: EspnLeague) -> LeagueSnapshot:
    teams = tuple(create_team_snapshot(team) for team in league.teams)

    matchups = tuple(
        create_matchup_snapshot(matchup, week=week)
        for week in range(1, league.settings.reg_season_count + 1)
        for matchup in league.box_scores(week)
    )

    return LeagueSnapshot(
        league_id=league.league_id,
        name=league.settings.name,
        season=league.year,
        current_week=league.current_week,
        regular_season_weeks=league.settings.reg_season_count,
        teams=teams,
        matchups=matchups,
    )
