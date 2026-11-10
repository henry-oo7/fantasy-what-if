from fastapi import status
from fastapi.testclient import TestClient

from app.analytics.optimal_lineup import (
    calculate_optimal_lineup_summaries,
)
from app.api.dependencies import (
    get_current_league_snapshot,
    get_persisted_optimal_lineup_summaries,
)
from app.domain.league import (
    LeagueSnapshot,
    MatchupSnapshot,
    TeamSnapshot,
)
from app.domain.lineup import (
    PlayerWeekSnapshot,
    TeamWeekRoster,
)
from app.main import app

client = TestClient(app)


def create_player(
    *,
    player_id: int,
    name: str,
    position: str,
    actual_slot: str,
    eligible_slots: tuple[str, ...],
    points: float,
    is_starter: bool = False,
    is_bench: bool = False,
) -> PlayerWeekSnapshot:
    return PlayerWeekSnapshot(
        player_id=player_id,
        player_name=name,
        pro_team="NFL",
        default_position=position,
        actual_lineup_slot=actual_slot,
        eligible_lineup_slots=eligible_slots,
        points=points,
        projected_points=None,
        is_starter=is_starter,
        is_bench=is_bench,
        is_injured_reserve=False,
        is_nfl_bye=False,
    )


def create_roster() -> TeamWeekRoster:
    return TeamWeekRoster(
        team_id=1,
        team_name="Team Alpha",
        week=1,
        players=(
            create_player(
                player_id=1,
                name="Starting QB",
                position="QB",
                actual_slot="QB",
                eligible_slots=("QB", "BENCH"),
                points=10.0,
                is_starter=True,
            ),
            create_player(
                player_id=2,
                name="Bench QB",
                position="QB",
                actual_slot="BENCH",
                eligible_slots=("QB", "BENCH"),
                points=20.0,
                is_bench=True,
            ),
            create_player(
                player_id=3,
                name="RB One",
                position="RB",
                actual_slot="RB",
                eligible_slots=("RB", "FLEX", "BENCH"),
                points=12.0,
                is_starter=True,
            ),
            create_player(
                player_id=4,
                name="RB Two",
                position="RB",
                actual_slot="RB",
                eligible_slots=("RB", "FLEX", "BENCH"),
                points=11.0,
                is_starter=True,
            ),
            create_player(
                player_id=5,
                name="WR One",
                position="WR",
                actual_slot="WR",
                eligible_slots=("WR", "FLEX", "BENCH"),
                points=14.0,
                is_starter=True,
            ),
            create_player(
                player_id=6,
                name="WR Two",
                position="WR",
                actual_slot="WR",
                eligible_slots=("WR", "FLEX", "BENCH"),
                points=13.0,
                is_starter=True,
            ),
            create_player(
                player_id=7,
                name="Starting TE",
                position="TE",
                actual_slot="TE",
                eligible_slots=("TE", "FLEX", "BENCH"),
                points=8.0,
                is_starter=True,
            ),
            create_player(
                player_id=8,
                name="Starting Flex",
                position="RB",
                actual_slot="FLEX",
                eligible_slots=("RB", "FLEX", "BENCH"),
                points=7.0,
                is_starter=True,
            ),
            create_player(
                player_id=9,
                name="Starting Kicker",
                position="K",
                actual_slot="K",
                eligible_slots=("K", "BENCH"),
                points=6.0,
                is_starter=True,
            ),
            create_player(
                player_id=10,
                name="Starting Defense",
                position="DST",
                actual_slot="DST",
                eligible_slots=("DST", "BENCH"),
                points=5.0,
                is_starter=True,
            ),
        ),
    )


def override_summaries():
    return calculate_optimal_lineup_summaries((create_roster(),))


def override_snapshot() -> LeagueSnapshot:
    return LeagueSnapshot(
        league_id=1,
        name="Test League",
        season=2026,
        current_week=2,
        regular_season_weeks=14,
        teams=(
            TeamSnapshot(
                team_id=1,
                name="Team Alpha",
                wins=0,
                losses=1,
                ties=0,
                points_for=86.0,
                points_against=90.0,
            ),
            TeamSnapshot(
                team_id=2,
                name="Team Bravo",
                wins=1,
                losses=0,
                ties=0,
                points_for=90.0,
                points_against=86.0,
            ),
        ),
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=86.0,
                away_score=90.0,
            ),
        ),
    )


def test_optimal_lineups_endpoint_returns_team_summaries() -> None:
    app.dependency_overrides[get_persisted_optimal_lineup_summaries] = override_summaries

    try:
        response = client.get("/api/analytics/optimal-lineups")
    finally:
        app.dependency_overrides.pop(
            get_persisted_optimal_lineup_summaries,
            None,
        )

    assert response.status_code == status.HTTP_200_OK

    summaries = response.json()

    assert len(summaries) == 1

    summary = summaries[0]

    assert summary["team_id"] == 1
    assert summary["team_name"] == "Team Alpha"
    assert summary["weeks_played"] == 1
    assert summary["actual_points"] == 86.0
    assert summary["optimal_points"] == 96.0
    assert summary["missed_points"] == 10.0
    assert summary["efficiency_percentage"] == 89.58
    assert len(summary["weekly_results"]) == 1


def test_team_optimal_lineup_endpoint_returns_requested_team() -> None:
    app.dependency_overrides[get_persisted_optimal_lineup_summaries] = override_summaries
    app.dependency_overrides[get_current_league_snapshot] = override_snapshot

    try:
        response = client.get("/api/analytics/optimal-lineups/1")
    finally:
        app.dependency_overrides.pop(
            get_persisted_optimal_lineup_summaries,
            None,
        )
        app.dependency_overrides.pop(
            get_current_league_snapshot,
            None,
        )

    assert response.status_code == status.HTTP_200_OK

    summary = response.json()

    assert summary["team_id"] == 1
    assert summary["team_name"] == "Team Alpha"

    week = summary["weekly_results"][0]

    assert week["week"] == 1
    assert week["actual_points"] == 86.0
    assert week["optimal_points"] == 96.0
    assert week["actual_starter_count"] == 9
    assert week["optimal_filled_slot_count"] == 9
    assert week["unfilled_slots"] == []
    assert week["opponent_team_id"] == 2
    assert week["opponent_team_name"] == "Team Bravo"
    assert week["opponent_points"] == 90.0
    assert week["actual_result"] == "loss"
    assert week["optimal_result"] == "win"
    assert week["result_flipped"] is True
    assert summary["actual_record"]["losses"] == 1
    assert summary["optimal_record"]["wins"] == 1
    assert summary["equivalent_wins_gained"] == 1.0
    assert summary["results_flipped"] == 1

    optimal_names = {player["player_name"] for player in week["optimal_lineup"]}

    assert "Bench QB" in optimal_names
    assert "Starting QB" not in optimal_names


def test_team_optimal_lineup_endpoint_returns_not_found() -> None:
    app.dependency_overrides[get_persisted_optimal_lineup_summaries] = override_summaries
    app.dependency_overrides[get_current_league_snapshot] = override_snapshot

    try:
        response = client.get("/api/analytics/optimal-lineups/999")
    finally:
        app.dependency_overrides.pop(
            get_persisted_optimal_lineup_summaries,
            None,
        )
        app.dependency_overrides.pop(
            get_current_league_snapshot,
            None,
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Team 999 was not found."}


def test_optimal_lineup_api_does_not_run_optimizer() -> None:
    from unittest.mock import patch

    # Build the test fixture before optimizer calls are blocked.
    persisted_summaries = override_summaries()

    def override_persisted_summaries():
        return persisted_summaries

    app.dependency_overrides[get_persisted_optimal_lineup_summaries] = override_persisted_summaries

    try:
        with patch(
            "app.analytics.optimal_lineup.calculate_team_week_optimal_lineup",
            side_effect=AssertionError("Optimizer must not run during GET."),
        ):
            response = client.get("/api/analytics/optimal-lineups")
    finally:
        app.dependency_overrides.pop(
            get_persisted_optimal_lineup_summaries,
            None,
        )

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 1
