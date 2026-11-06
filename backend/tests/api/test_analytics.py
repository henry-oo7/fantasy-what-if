from fastapi import status
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_league_snapshot
from app.domain.league import LeagueSnapshot, MatchupSnapshot, TeamSnapshot
from app.main import app

client = TestClient(app)


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


def create_test_snapshot() -> LeagueSnapshot:
    return LeagueSnapshot(
        league_id=529250970,
        name="Jimmy and Friends",
        season=2026,
        current_week=2,
        regular_season_weeks=12,
        teams=(
            create_team(
                team_id=1,
                name="Team Alpha",
            ),
            create_team(
                team_id=2,
                name="Team Bravo",
            ),
            create_team(
                team_id=3,
                name="Team Charlie",
            ),
            create_team(
                team_id=4,
                name="Team Delta",
            ),
        ),
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


def test_all_play_endpoint_returns_ranked_standings() -> None:
    snapshot = create_test_snapshot()

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get("/api/analytics/all-play")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [
        {
            "rank": 1,
            "team_id": 1,
            "team_name": "Team Alpha",
            "wins": 3,
            "losses": 0,
            "ties": 0,
            "points_for": 120.0,
            "win_percentage": 1.0,
        },
        {
            "rank": 2,
            "team_id": 2,
            "team_name": "Team Bravo",
            "wins": 2,
            "losses": 1,
            "ties": 0,
            "points_for": 110.0,
            "win_percentage": 0.6667,
        },
        {
            "rank": 3,
            "team_id": 3,
            "team_name": "Team Charlie",
            "wins": 1,
            "losses": 2,
            "ties": 0,
            "points_for": 100.0,
            "win_percentage": 0.3333,
        },
        {
            "rank": 4,
            "team_id": 4,
            "team_name": "Team Delta",
            "wins": 0,
            "losses": 3,
            "ties": 0,
            "points_for": 90.0,
            "win_percentage": 0.0,
        },
    ]


def test_all_play_endpoint_excludes_current_week() -> None:
    snapshot = LeagueSnapshot(
        league_id=529250970,
        name="Jimmy and Friends",
        season=2026,
        current_week=1,
        regular_season_weeks=12,
        teams=(
            create_team(
                team_id=1,
                name="Team Alpha",
            ),
            create_team(
                team_id=2,
                name="Team Bravo",
            ),
        ),
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=110.0,
            ),
        ),
    )

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get("/api/analytics/all-play")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_200_OK

    standings = response.json()

    assert len(standings) == 2
    assert all(standing["wins"] == 0 for standing in standings)
    assert all(standing["losses"] == 0 for standing in standings)
    assert all(standing["ties"] == 0 for standing in standings)
    assert all(standing["points_for"] == 0.0 for standing in standings)
    assert all(standing["win_percentage"] == 0.0 for standing in standings)


def test_schedule_swap_endpoint_returns_hypothetical_records() -> None:
    snapshot = create_test_snapshot()

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get(
            "/api/analytics/schedule-swap",
            params={
                "first_team_id": 1,
                "second_team_id": 2,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_200_OK

    result = response.json()

    assert result["completed_weeks"] == [1]

    assert result["first_team"]["team_id"] == 1
    assert result["first_team"]["team_name"] == "Team Alpha"
    assert result["first_team"]["borrowed_schedule_team_id"] == 2
    assert result["first_team"]["original_record"] == {
        "wins": 1,
        "losses": 0,
        "ties": 0,
        "win_percentage": 1.0,
    }
    assert result["first_team"]["swapped_record"] == {
        "wins": 1,
        "losses": 0,
        "ties": 0,
        "win_percentage": 1.0,
    }

    assert result["second_team"]["team_id"] == 2
    assert result["second_team"]["team_name"] == "Team Bravo"
    assert result["second_team"]["original_record"] == {
        "wins": 0,
        "losses": 1,
        "ties": 0,
        "win_percentage": 0.0,
    }
    assert result["second_team"]["swapped_record"] == {
        "wins": 0,
        "losses": 1,
        "ties": 0,
        "win_percentage": 0.0,
    }


def test_schedule_swap_endpoint_rejects_same_team() -> None:
    snapshot = create_test_snapshot()

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get(
            "/api/analytics/schedule-swap",
            params={
                "first_team_id": 1,
                "second_team_id": 1,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "Schedule swaps require two different teams."}


def test_schedule_swap_endpoint_rejects_unknown_team() -> None:
    snapshot = create_test_snapshot()

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get(
            "/api/analytics/schedule-swap",
            params={
                "first_team_id": 1,
                "second_team_id": 999,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "Unknown team ID values: 999"}


def test_schedule_swap_endpoint_requires_both_team_ids() -> None:
    snapshot = create_test_snapshot()

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get("/api/analytics/schedule-swap")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_league_average_endpoint_returns_combined_records() -> None:
    snapshot = create_test_snapshot()

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get("/api/analytics/league-average")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_200_OK

    standings = response.json()

    assert len(standings) == 4

    alpha = standings[0]

    assert alpha["rank"] == 1
    assert alpha["team_id"] == 1
    assert alpha["team_name"] == "Team Alpha"
    assert alpha["weeks_played"] == 1
    assert alpha["points_for"] == 120.0
    assert alpha["average_points_per_week"] == 120.0

    assert alpha["actual_record"] == {
        "wins": 1,
        "losses": 0,
        "ties": 0,
        "win_percentage": 1.0,
    }

    assert alpha["average_record"] == {
        "wins": 1,
        "losses": 0,
        "ties": 0,
        "win_percentage": 1.0,
    }

    assert alpha["combined_record"] == {
        "wins": 2,
        "losses": 0,
        "ties": 0,
        "win_percentage": 1.0,
    }

    assert alpha["weekly_results"] == [
        {
            "week": 1,
            "team_score": 120.0,
            "league_average": 105.0,
            "actual_opponent_id": 2,
            "actual_opponent_name": "Team Bravo",
            "actual_opponent_score": 110.0,
            "actual_result": "win",
            "average_result": "win",
        }
    ]


def test_league_average_endpoint_excludes_current_week() -> None:
    snapshot = LeagueSnapshot(
        league_id=529250970,
        name="Jimmy and Friends",
        season=2026,
        current_week=1,
        regular_season_weeks=12,
        teams=(
            create_team(
                team_id=1,
                name="Team Alpha",
            ),
            create_team(
                team_id=2,
                name="Team Bravo",
            ),
        ),
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=110.0,
            ),
        ),
    )

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get("/api/analytics/league-average")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_200_OK

    standings = response.json()

    assert len(standings) == 2
    assert all(standing["weeks_played"] == 0 for standing in standings)
    assert all(
        standing["combined_record"]
        == {
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "win_percentage": 0.0,
        }
        for standing in standings
    )
