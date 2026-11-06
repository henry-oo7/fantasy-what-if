import pytest

from app.analytics.schedule_swap import (
    calculate_schedule_swap,
)
from app.domain.league import (
    LeagueSnapshot,
    MatchupSnapshot,
    TeamSnapshot,
)


def create_team(
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


def create_snapshot() -> LeagueSnapshot:
    return LeagueSnapshot(
        league_id=529250970,
        name="Test League",
        season=2026,
        current_week=4,
        regular_season_weeks=12,
        teams=(
            create_team(1, "Team Alpha"),
            create_team(2, "Team Bravo"),
            create_team(3, "Team Charlie"),
            create_team(4, "Team Delta"),
        ),
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=100.0,
                away_score=90.0,
            ),
            MatchupSnapshot(
                week=1,
                home_team_id=3,
                away_team_id=4,
                home_score=80.0,
                away_score=70.0,
            ),
            MatchupSnapshot(
                week=2,
                home_team_id=1,
                away_team_id=3,
                home_score=75.0,
                away_score=95.0,
            ),
            MatchupSnapshot(
                week=2,
                home_team_id=2,
                away_team_id=4,
                home_score=85.0,
                away_score=65.0,
            ),
            MatchupSnapshot(
                week=3,
                home_team_id=1,
                away_team_id=4,
                home_score=110.0,
                away_score=105.0,
            ),
            MatchupSnapshot(
                week=3,
                home_team_id=2,
                away_team_id=3,
                home_score=100.0,
                away_score=120.0,
            ),
        ),
    )


def test_schedule_swap_recalculates_both_records() -> None:
    result = calculate_schedule_swap(
        create_snapshot(),
        first_team_id=1,
        second_team_id=2,
    )

    assert result.completed_weeks == (1, 2, 3)

    assert result.first_team.original_record.wins == 2
    assert result.first_team.original_record.losses == 1
    assert result.first_team.swapped_record.wins == 2
    assert result.first_team.swapped_record.losses == 1

    assert result.second_team.original_record.wins == 1
    assert result.second_team.original_record.losses == 2
    assert result.second_team.swapped_record.wins == 0
    assert result.second_team.swapped_record.losses == 3


def test_schedule_swap_keeps_head_to_head_week_valid() -> None:
    result = calculate_schedule_swap(
        create_snapshot(),
        first_team_id=1,
        second_team_id=2,
    )

    first_week = result.first_team.weekly_results[0]
    second_week = result.second_team.weekly_results[0]

    assert first_week.hypothetical_opponent_id == 2
    assert first_week.result == "win"

    assert second_week.hypothetical_opponent_id == 1
    assert second_week.result == "loss"


def test_schedule_swap_uses_borrowed_opponents() -> None:
    result = calculate_schedule_swap(
        create_snapshot(),
        first_team_id=1,
        second_team_id=2,
    )

    first_team_week_two = result.first_team.weekly_results[1]
    second_team_week_two = result.second_team.weekly_results[1]

    assert first_team_week_two.hypothetical_opponent_id == 4
    assert first_team_week_two.team_score == 75.0
    assert first_team_week_two.opponent_score == 65.0
    assert first_team_week_two.result == "win"

    assert second_team_week_two.hypothetical_opponent_id == 3
    assert second_team_week_two.team_score == 85.0
    assert second_team_week_two.opponent_score == 95.0
    assert second_team_week_two.result == "loss"


def test_schedule_swap_rejects_the_same_team() -> None:
    with pytest.raises(
        ValueError,
        match="two different teams",
    ):
        calculate_schedule_swap(
            create_snapshot(),
            first_team_id=1,
            second_team_id=1,
        )


def test_schedule_swap_rejects_unknown_team() -> None:
    with pytest.raises(
        ValueError,
        match="Unknown team ID",
    ):
        calculate_schedule_swap(
            create_snapshot(),
            first_team_id=1,
            second_team_id=999,
        )
