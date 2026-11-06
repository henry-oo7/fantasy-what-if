from dataclasses import dataclass

from app.domain.league import LeagueSnapshot, MatchupSnapshot, TeamSnapshot
from app.espn.adapter import (
    create_league_snapshot,
    create_matchup_snapshot,
    create_team_snapshot,
)


@dataclass
class FakeTeam:
    team_id: int
    team_name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float


@dataclass
class FakeMatchup:
    home_team: FakeTeam
    away_team: FakeTeam
    home_score: float
    away_score: float


@dataclass
class FakeSettings:
    name: str
    reg_season_count: int


class FakeLeague:
    def __init__(
        self,
        *,
        league_id: int,
        year: int,
        current_week: int,
        settings: FakeSettings,
        teams: list[FakeTeam],
        matchups_by_week: dict[int, list[FakeMatchup]],
    ) -> None:
        self.league_id = league_id
        self.year = year
        self.current_week = current_week
        self.settings = settings
        self.teams = teams
        self.matchups_by_week = matchups_by_week
        self.requested_weeks: list[int] = []

    def box_scores(self, week: int) -> list[FakeMatchup]:
        self.requested_weeks.append(week)
        return self.matchups_by_week.get(week, [])


def create_fake_team(
    *,
    team_id: int = 1,
    team_name: str = "LIFE GIBBS AND TAKE",
    wins: int = 6,
    losses: int = 2,
    ties: int = 0,
    points_for: float = 878.52,
    points_against: float = 743.44,
) -> FakeTeam:
    return FakeTeam(
        team_id=team_id,
        team_name=team_name,
        wins=wins,
        losses=losses,
        ties=ties,
        points_for=points_for,
        points_against=points_against,
    )


def test_create_team_snapshot_converts_espn_team() -> None:
    team = create_fake_team()

    snapshot = create_team_snapshot(team)

    assert snapshot == TeamSnapshot(
        team_id=1,
        name="LIFE GIBBS AND TAKE",
        wins=6,
        losses=2,
        ties=0,
        points_for=878.52,
        points_against=743.44,
    )


def test_create_matchup_snapshot_converts_espn_matchup() -> None:
    home_team = create_fake_team(
        team_id=11,
        team_name="Sup My Njigba",
    )
    away_team = create_fake_team(
        team_id=10,
        team_name="Clown lives matter",
    )
    matchup = FakeMatchup(
        home_team=home_team,
        away_team=away_team,
        home_score=90.38,
        away_score=68.98,
    )

    snapshot = create_matchup_snapshot(matchup, week=8)

    assert snapshot == MatchupSnapshot(
        week=8,
        home_team_id=11,
        away_team_id=10,
        home_score=90.38,
        away_score=68.98,
    )


def test_create_league_snapshot_converts_league_data() -> None:
    home_team = create_fake_team(
        team_id=1,
        team_name="LIFE GIBBS AND TAKE",
    )
    away_team = create_fake_team(
        team_id=4,
        team_name="Kamara Sutra",
        wins=3,
        losses=5,
        points_for=830.02,
        points_against=957.34,
    )

    matchup = FakeMatchup(
        home_team=home_team,
        away_team=away_team,
        home_score=144.80,
        away_score=122.28,
    )
    league = FakeLeague(
        league_id=529250970,
        year=2026,
        current_week=9,
        settings=FakeSettings(
            name="Jimmy and Friends",
            reg_season_count=12,
        ),
        teams=[home_team, away_team],
        matchups_by_week={
            8: [matchup],
        },
    )

    snapshot = create_league_snapshot(league)

    assert snapshot == LeagueSnapshot(
        league_id=529250970,
        name="Jimmy and Friends",
        season=2026,
        current_week=9,
        regular_season_weeks=12,
        teams=(
            TeamSnapshot(
                team_id=1,
                name="LIFE GIBBS AND TAKE",
                wins=6,
                losses=2,
                ties=0,
                points_for=878.52,
                points_against=743.44,
            ),
            TeamSnapshot(
                team_id=4,
                name="Kamara Sutra",
                wins=3,
                losses=5,
                ties=0,
                points_for=830.02,
                points_against=957.34,
            ),
        ),
        matchups=(
            MatchupSnapshot(
                week=8,
                home_team_id=1,
                away_team_id=4,
                home_score=144.80,
                away_score=122.28,
            ),
        ),
    )


def test_create_league_snapshot_requests_every_regular_season_week() -> None:
    league = FakeLeague(
        league_id=529250970,
        year=2026,
        current_week=9,
        settings=FakeSettings(
            name="Jimmy and Friends",
            reg_season_count=12,
        ),
        teams=[],
        matchups_by_week={},
    )

    create_league_snapshot(league)

    assert league.requested_weeks == list(range(1, 13))
