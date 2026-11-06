from dataclasses import FrozenInstanceError

import pytest

from app.domain.league import LeagueSnapshot, MatchupSnapshot, TeamSnapshot


def create_team_snapshot(
    *,
    team_id: int = 1,
    name: str = "Test Team",
    wins: int = 6,
    losses: int = 2,
    ties: int = 0,
    points_for: float = 878.52,
    points_against: float = 743.44,
) -> TeamSnapshot:
    return TeamSnapshot(
        team_id=team_id,
        name=name,
        wins=wins,
        losses=losses,
        ties=ties,
        points_for=points_for,
        points_against=points_against,
    )


def create_matchup_snapshot(
    *,
    week: int = 8,
    home_team_id: int = 1,
    away_team_id: int = 2,
    home_score: float = 144.80,
    away_score: float = 122.28,
) -> MatchupSnapshot:
    return MatchupSnapshot(
        week=week,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_score=home_score,
        away_score=away_score,
    )


def test_team_snapshot_stores_team_data() -> None:
    team = create_team_snapshot(
        team_id=7,
        name="LIFE GIBBS AND TAKE",
        wins=6,
        losses=2,
        ties=0,
        points_for=878.52,
        points_against=743.44,
    )

    assert team.team_id == 7
    assert team.name == "LIFE GIBBS AND TAKE"
    assert team.wins == 6
    assert team.losses == 2
    assert team.ties == 0
    assert team.points_for == 878.52
    assert team.points_against == 743.44


def test_matchup_snapshot_stores_matchup_data() -> None:
    matchup = create_matchup_snapshot(
        week=8,
        home_team_id=7,
        away_team_id=4,
        home_score=144.80,
        away_score=122.28,
    )

    assert matchup.week == 8
    assert matchup.home_team_id == 7
    assert matchup.away_team_id == 4
    assert matchup.home_score == 144.80
    assert matchup.away_score == 122.28


def test_league_snapshot_stores_teams_and_matchups_as_tuples() -> None:
    home_team = create_team_snapshot(
        team_id=7,
        name="LIFE GIBBS AND TAKE",
    )

    away_team = create_team_snapshot(
        team_id=4,
        name="Kamara Sutra",
        wins=3,
        losses=5,
        points_for=830.02,
        points_against=957.34,
    )

    matchup = create_matchup_snapshot(
        home_team_id=home_team.team_id,
        away_team_id=away_team.team_id,
    )

    league = LeagueSnapshot(
        league_id=529250970,
        name="Jimmy and Friends",
        season=2026,
        current_week=9,
        regular_season_weeks=12,
        teams=(home_team, away_team),
        matchups=(matchup,),
    )

    assert league.league_id == 529250970
    assert league.name == "Jimmy and Friends"
    assert league.season == 2026
    assert league.current_week == 9
    assert league.regular_season_weeks == 12

    assert isinstance(league.teams, tuple)
    assert isinstance(league.matchups, tuple)

    assert league.teams == (home_team, away_team)
    assert league.matchups == (matchup,)


@pytest.mark.parametrize(
    ("snapshot", "field_name", "replacement_value"),
    [
        (
            create_team_snapshot(),
            "name",
            "Changed Team",
        ),
        (
            create_matchup_snapshot(),
            "home_score",
            200.0,
        ),
        (
            LeagueSnapshot(
                league_id=529250970,
                name="Jimmy and Friends",
                season=2026,
                current_week=9,
                regular_season_weeks=12,
                teams=(),
                matchups=(),
            ),
            "current_week",
            10,
        ),
    ],
)
def test_snapshots_reject_field_reassignment(
    snapshot: object,
    field_name: str,
    replacement_value: object,
) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, field_name, replacement_value)
