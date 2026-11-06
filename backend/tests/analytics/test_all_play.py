from dataclasses import FrozenInstanceError

import pytest

from app.analytics.all_play import (
    AllPlayStanding,
    calculate_all_play_standings,
)
from app.domain.league import LeagueSnapshot, MatchupSnapshot, TeamSnapshot


def create_team(
    *,
    team_id: int,
    name: str,
) -> TeamSnapshot:
    return TeamSnapshot(
        team_id=team_id,
        name=name,
        wins=0,
        losses=0,
        ties=0,
        points_for=0.0,
        points_against=0.0,
    )


def create_snapshot(
    *,
    current_week: int = 3,
    matchups: tuple[MatchupSnapshot, ...],
) -> LeagueSnapshot:
    return LeagueSnapshot(
        league_id=529250970,
        name="Jimmy and Friends",
        season=2026,
        current_week=current_week,
        regular_season_weeks=12,
        teams=(
            create_team(team_id=1, name="Team Alpha"),
            create_team(team_id=2, name="Team Bravo"),
            create_team(team_id=3, name="Team Charlie"),
            create_team(team_id=4, name="Team Delta"),
        ),
        matchups=matchups,
    )


def test_calculate_all_play_standings_compares_every_team_each_week() -> None:
    snapshot = create_snapshot(
        current_week=2,
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=110.0,
            ),
            MatchupSnapshot(
                week=1,
                home_team_id=3,
                away_team_id=4,
                home_score=100.0,
                away_score=90.0,
            ),
        ),
    )

    standings = calculate_all_play_standings(snapshot)

    assert standings == (
        AllPlayStanding(
            rank=1,
            team_id=1,
            team_name="Team Alpha",
            wins=3,
            losses=0,
            ties=0,
            points_for=120.0,
            win_percentage=1.0,
        ),
        AllPlayStanding(
            rank=2,
            team_id=2,
            team_name="Team Bravo",
            wins=2,
            losses=1,
            ties=0,
            points_for=110.0,
            win_percentage=0.6667,
        ),
        AllPlayStanding(
            rank=3,
            team_id=3,
            team_name="Team Charlie",
            wins=1,
            losses=2,
            ties=0,
            points_for=100.0,
            win_percentage=0.3333,
        ),
        AllPlayStanding(
            rank=4,
            team_id=4,
            team_name="Team Delta",
            wins=0,
            losses=3,
            ties=0,
            points_for=90.0,
            win_percentage=0.0,
        ),
    )


def test_calculate_all_play_standings_accumulates_multiple_weeks() -> None:
    snapshot = create_snapshot(
        current_week=3,
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=110.0,
            ),
            MatchupSnapshot(
                week=1,
                home_team_id=3,
                away_team_id=4,
                home_score=100.0,
                away_score=90.0,
            ),
            MatchupSnapshot(
                week=2,
                home_team_id=1,
                away_team_id=3,
                home_score=80.0,
                away_score=130.0,
            ),
            MatchupSnapshot(
                week=2,
                home_team_id=2,
                away_team_id=4,
                home_score=105.0,
                away_score=95.0,
            ),
        ),
    )

    standings = calculate_all_play_standings(snapshot)
    standings_by_team = {standing.team_id: standing for standing in standings}

    assert standings_by_team[1].wins == 3
    assert standings_by_team[1].losses == 3
    assert standings_by_team[1].ties == 0
    assert standings_by_team[1].points_for == 200.0

    assert standings_by_team[2].wins == 4
    assert standings_by_team[2].losses == 2
    assert standings_by_team[2].ties == 0
    assert standings_by_team[2].points_for == 215.0

    assert standings_by_team[3].wins == 4
    assert standings_by_team[3].losses == 2
    assert standings_by_team[3].ties == 0
    assert standings_by_team[3].points_for == 230.0

    assert standings_by_team[4].wins == 1
    assert standings_by_team[4].losses == 5
    assert standings_by_team[4].ties == 0
    assert standings_by_team[4].points_for == 185.0


def test_calculate_all_play_standings_handles_equal_scores_as_ties() -> None:
    snapshot = create_snapshot(
        current_week=2,
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=100.0,
                away_score=100.0,
            ),
            MatchupSnapshot(
                week=1,
                home_team_id=3,
                away_team_id=4,
                home_score=90.0,
                away_score=80.0,
            ),
        ),
    )

    standings = calculate_all_play_standings(snapshot)
    standings_by_team = {standing.team_id: standing for standing in standings}

    assert standings_by_team[1].wins == 2
    assert standings_by_team[1].losses == 0
    assert standings_by_team[1].ties == 1
    assert standings_by_team[1].win_percentage == 0.8333

    assert standings_by_team[2].wins == 2
    assert standings_by_team[2].losses == 0
    assert standings_by_team[2].ties == 1
    assert standings_by_team[2].win_percentage == 0.8333

    assert standings_by_team[3].wins == 1
    assert standings_by_team[3].losses == 2
    assert standings_by_team[3].ties == 0
    assert standings_by_team[3].win_percentage == 0.3333

    assert standings_by_team[4].wins == 0
    assert standings_by_team[4].losses == 3
    assert standings_by_team[4].ties == 0
    assert standings_by_team[4].win_percentage == 0.0


def test_calculate_all_play_standings_excludes_current_and_future_weeks() -> None:
    snapshot = create_snapshot(
        current_week=2,
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=110.0,
            ),
            MatchupSnapshot(
                week=1,
                home_team_id=3,
                away_team_id=4,
                home_score=100.0,
                away_score=90.0,
            ),
            MatchupSnapshot(
                week=2,
                home_team_id=1,
                away_team_id=2,
                home_score=0.0,
                away_score=0.0,
            ),
            MatchupSnapshot(
                week=2,
                home_team_id=3,
                away_team_id=4,
                home_score=0.0,
                away_score=0.0,
            ),
            MatchupSnapshot(
                week=3,
                home_team_id=1,
                away_team_id=3,
                home_score=0.0,
                away_score=0.0,
            ),
            MatchupSnapshot(
                week=3,
                home_team_id=2,
                away_team_id=4,
                home_score=0.0,
                away_score=0.0,
            ),
        ),
    )

    standings = calculate_all_play_standings(snapshot)

    assert all(standing.wins + standing.losses + standing.ties == 3 for standing in standings)

    assert standings[0].team_id == 1
    assert standings[0].points_for == 120.0


def test_points_for_breaks_equal_win_percentage() -> None:
    snapshot = create_snapshot(
        current_week=3,
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=110.0,
            ),
            MatchupSnapshot(
                week=1,
                home_team_id=3,
                away_team_id=4,
                home_score=100.0,
                away_score=90.0,
            ),
            MatchupSnapshot(
                week=2,
                home_team_id=1,
                away_team_id=2,
                home_score=80.0,
                away_score=90.0,
            ),
            MatchupSnapshot(
                week=2,
                home_team_id=3,
                away_team_id=4,
                home_score=100.0,
                away_score=110.0,
            ),
        ),
    )

    standings = calculate_all_play_standings(snapshot)

    alpha = next(standing for standing in standings if standing.team_id == 1)
    charlie = next(standing for standing in standings if standing.team_id == 3)

    assert alpha.win_percentage == charlie.win_percentage
    assert alpha.points_for == charlie.points_for
    assert alpha.rank < charlie.rank


def test_teams_with_no_completed_scores_receive_zero_records() -> None:
    snapshot = create_snapshot(
        current_week=1,
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=0.0,
                away_score=0.0,
            ),
        ),
    )

    standings = calculate_all_play_standings(snapshot)

    assert len(standings) == 4

    for standing in standings:
        assert standing.wins == 0
        assert standing.losses == 0
        assert standing.ties == 0
        assert standing.points_for == 0.0
        assert standing.win_percentage == 0.0


def test_all_play_standing_is_immutable() -> None:
    standing = AllPlayStanding(
        rank=1,
        team_id=1,
        team_name="Team Alpha",
        wins=3,
        losses=0,
        ties=0,
        points_for=120.0,
        win_percentage=1.0,
    )

    with pytest.raises(FrozenInstanceError):
        standing.rank = 2
