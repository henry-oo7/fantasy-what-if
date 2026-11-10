from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.analytics.optimal_lineup import (
    LineupPlayer,
    LineupSubstitution,
    TeamWeekOptimalLineup,
)
from app.db.models import OptimalLineupResult
from app.repositories.optimal_lineup_repository import (
    OPTIMAL_LINEUP_CALCULATION_VERSION,
    load_optimal_lineup_summaries,
    load_optimal_lineup_week_results,
    replace_optimal_lineup_results,
)


def weekly_result(
    *,
    team_id: int = 7,
    team_name: str = "Team Alpha",
    week: int = 1,
    actual_points: float = 86.0,
    optimal_points: float = 96.0,
) -> TeamWeekOptimalLineup:
    return TeamWeekOptimalLineup(
        team_id=team_id,
        team_name=team_name,
        week=week,
        actual_points=actual_points,
        optimal_points=optimal_points,
        missed_points=round(
            optimal_points - actual_points,
            2,
        ),
        efficiency_percentage=round(
            actual_points / optimal_points * 100,
            2,
        ),
        actual_starter_count=9,
        optimal_filled_slot_count=9,
        unfilled_slots=(),
        actual_lineup=(
            LineupPlayer(
                lineup_slot="QB",
                player_id=100,
                player_name="Starting QB",
                default_position="QB",
                pro_team="NFL",
                points=10.0,
                actual_lineup_slot="QB",
                was_actual_starter=True,
                is_unfilled=False,
            ),
        ),
        optimal_lineup=(
            LineupPlayer(
                lineup_slot="QB",
                player_id=101,
                player_name="Bench QB",
                default_position="QB",
                pro_team="NFL",
                points=20.0,
                actual_lineup_slot="BE",
                was_actual_starter=False,
                is_unfilled=False,
            ),
        ),
        substitutions=(
            LineupSubstitution(
                lineup_slot="QB",
                player_in_id=101,
                player_in_name="Bench QB",
                player_out_id=100,
                player_out_name="Starting QB",
                point_change=10.0,
            ),
        ),
    )


def stored_result(
    *,
    team_id: int = 7,
    team_name: str = "Team Alpha",
    week: int = 1,
    actual_points: str = "86.00",
    optimal_points: str = "96.00",
) -> OptimalLineupResult:
    return OptimalLineupResult(
        id=1,
        season_id=4,
        team_id=team_id,
        week=week,
        team_name=team_name,
        actual_points=Decimal(actual_points),
        optimal_points=Decimal(optimal_points),
        missed_points=Decimal(str(float(optimal_points) - float(actual_points))),
        efficiency_percentage=Decimal(
            str(
                round(
                    float(actual_points) / float(optimal_points) * 100,
                    2,
                )
            )
        ),
        actual_starter_count=9,
        optimal_filled_slot_count=9,
        unfilled_slots=[],
        actual_lineup=[
            {
                "lineup_slot": "QB",
                "player_id": 100,
                "player_name": "Starting QB",
                "default_position": "QB",
                "pro_team": "NFL",
                "points": 10.0,
                "actual_lineup_slot": "QB",
                "was_actual_starter": True,
                "is_unfilled": False,
            }
        ],
        optimal_lineup=[
            {
                "lineup_slot": "QB",
                "player_id": 101,
                "player_name": "Bench QB",
                "default_position": "QB",
                "pro_team": "NFL",
                "points": 20.0,
                "actual_lineup_slot": "BE",
                "was_actual_starter": False,
                "is_unfilled": False,
            }
        ],
        substitutions=[
            {
                "lineup_slot": "QB",
                "player_in_id": 101,
                "player_in_name": "Bench QB",
                "player_out_id": 100,
                "player_out_name": "Starting QB",
                "point_change": 10.0,
            }
        ],
        calculation_version=(OPTIMAL_LINEUP_CALCULATION_VERSION),
    )


def test_replace_serializes_complete_result() -> None:
    session = MagicMock()

    stored = replace_optimal_lineup_results(
        session,
        season_id=4,
        weeks=(1,),
        results=(weekly_result(),),
    )

    assert len(stored) == 1

    result = stored[0]

    assert result.season_id == 4
    assert result.team_id == 7
    assert result.week == 1
    assert result.actual_points == Decimal("86.0")
    assert result.optimal_points == Decimal("96.0")
    assert result.actual_lineup[0]["player_name"] == "Starting QB"
    assert result.optimal_lineup[0]["player_name"] == "Bench QB"
    assert result.substitutions[0]["point_change"] == 10.0
    assert result.calculation_version == 3

    session.add.assert_called_once_with(result)
    session.flush.assert_called_once()


def test_replace_rejects_unrequested_week() -> None:
    session = MagicMock()

    with pytest.raises(
        ValueError,
        match="unrequested week",
    ):
        replace_optimal_lineup_results(
            session,
            season_id=4,
            weeks=(1,),
            results=(weekly_result(week=2),),
        )


def test_replace_rejects_duplicate_team_week() -> None:
    session = MagicMock()

    with pytest.raises(
        ValueError,
        match="Duplicate",
    ):
        replace_optimal_lineup_results(
            session,
            season_id=4,
            weeks=(1,),
            results=(
                weekly_result(),
                weekly_result(),
            ),
        )


def test_load_reconstructs_domain_result() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = [stored_result()]

    results = load_optimal_lineup_week_results(
        session,
        season_id=4,
    )

    assert len(results) == 1

    result = results[0]

    assert result.team_id == 7
    assert result.week == 1
    assert result.actual_points == 86.0
    assert result.optimal_points == 96.0
    assert result.actual_lineup[0].player_name == "Starting QB"
    assert result.optimal_lineup[0].player_name == "Bench QB"
    assert result.substitutions[0].point_change == 10.0


def test_load_summaries_aggregates_weeks() -> None:
    session = MagicMock()

    session.scalars.return_value.all.return_value = [
        stored_result(
            week=1,
            actual_points="86.00",
            optimal_points="96.00",
        ),
        stored_result(
            week=2,
            actual_points="90.00",
            optimal_points="100.00",
        ),
    ]

    summaries = load_optimal_lineup_summaries(
        session,
        season_id=4,
    )

    assert len(summaries) == 1

    summary = summaries[0]

    assert summary.team_id == 7
    assert summary.weeks_played == 2
    assert summary.actual_points == 176.0
    assert summary.optimal_points == 196.0
    assert summary.missed_points == 20.0
    assert summary.efficiency_percentage == 89.8
    assert tuple(result.week for result in summary.weekly_results) == (
        1,
        2,
    )


def test_empty_storage_returns_no_summaries() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []

    assert (
        load_optimal_lineup_summaries(
            session,
            season_id=4,
        )
        == ()
    )


def test_roster_loader_supports_exact_weeks() -> None:
    from app.repositories.player_week_repository import (
        load_team_week_rosters,
    )

    session = MagicMock()
    session.execute.return_value.all.return_value = []

    result = load_team_week_rosters(
        session,
        season_id=4,
        weeks=(8, 8),
    )

    assert result == ()

    statement = str(session.execute.call_args.args[0])

    assert "player_week_scores.week IN" in statement


def test_roster_loader_rejects_invalid_week() -> None:
    from app.repositories.player_week_repository import (
        load_team_week_rosters,
    )

    session = MagicMock()

    with pytest.raises(
        ValueError,
        match="positive integers",
    ):
        load_team_week_rosters(
            session,
            season_id=4,
            weeks=(0,),
        )
