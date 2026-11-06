from fastapi import status
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_completed_team_week_rosters,
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
                eligible_slots=("QB", "BE"),
                points=10.0,
                is_starter=True,
            ),
            create_player(
                player_id=2,
                name="Bench QB",
                position="QB",
                actual_slot="BE",
                eligible_slots=("QB", "BE"),
                points=20.0,
                is_bench=True,
            ),
            create_player(
                player_id=3,
                name="RB One",
                position="RB",
                actual_slot="RB",
                eligible_slots=("RB", "RB/WR/TE", "BE"),
                points=12.0,
                is_starter=True,
            ),
            create_player(
                player_id=4,
                name="RB Two",
                position="RB",
                actual_slot="RB",
                eligible_slots=("RB", "RB/WR/TE", "BE"),
                points=11.0,
                is_starter=True,
            ),
            create_player(
                player_id=5,
                name="WR One",
                position="WR",
                actual_slot="WR",
                eligible_slots=("WR", "RB/WR/TE", "BE"),
                points=14.0,
                is_starter=True,
            ),
            create_player(
                player_id=6,
                name="WR Two",
                position="WR",
                actual_slot="WR",
                eligible_slots=("WR", "RB/WR/TE", "BE"),
                points=13.0,
                is_starter=True,
            ),
            create_player(
                player_id=7,
                name="Starting TE",
                position="TE",
                actual_slot="TE",
                eligible_slots=("TE", "RB/WR/TE", "BE"),
                points=8.0,
                is_starter=True,
            ),
            create_player(
                player_id=8,
                name="Starting Flex",
                position="RB",
                actual_slot="RB/WR/TE",
                eligible_slots=("RB", "RB/WR/TE", "BE"),
                points=7.0,
                is_starter=True,
            ),
            create_player(
                player_id=9,
                name="Starting Kicker",
                position="K",
                actual_slot="K",
                eligible_slots=("K", "BE"),
                points=6.0,
                is_starter=True,
            ),
            create_player(
                player_id=10,
                name="Starting Defense",
                position="D/ST",
                actual_slot="D/ST",
                eligible_slots=("D/ST", "BE"),
                points=5.0,
                is_starter=True,
            ),
        ),
    )


def override_rosters() -> tuple[TeamWeekRoster, ...]:
    return (create_roster(),)


def test_optimal_lineups_endpoint_returns_team_summaries() -> None:
    app.dependency_overrides[get_completed_team_week_rosters] = override_rosters

    try:
        response = client.get("/api/analytics/optimal-lineups")
    finally:
        app.dependency_overrides.clear()

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
    app.dependency_overrides[get_completed_team_week_rosters] = override_rosters

    try:
        response = client.get("/api/analytics/optimal-lineups/1")
    finally:
        app.dependency_overrides.clear()

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

    optimal_names = {player["player_name"] for player in week["optimal_lineup"]}

    assert "Bench QB" in optimal_names
    assert "Starting QB" not in optimal_names


def test_team_optimal_lineup_endpoint_returns_not_found() -> None:
    app.dependency_overrides[get_completed_team_week_rosters] = override_rosters

    try:
        response = client.get("/api/analytics/optimal-lineups/999")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Team 999 was not found."}
