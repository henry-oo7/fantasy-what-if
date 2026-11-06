from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_league_snapshot
from app.domain.league import LeagueSnapshot, MatchupSnapshot, TeamSnapshot
from app.main import app

client = TestClient(app)


def create_test_snapshot() -> LeagueSnapshot:
    return LeagueSnapshot(
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


def test_health_endpoint_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_current_league_endpoint_returns_snapshot() -> None:
    snapshot = create_test_snapshot()

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get("/api/leagues/current")
    finally:
        app.dependency_overrides.pop(
            get_current_league_snapshot,
            None,
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "league_id": 529250970,
        "name": "Jimmy and Friends",
        "season": 2026,
        "current_week": 9,
        "regular_season_weeks": 12,
        "teams": [
            {
                "team_id": 1,
                "name": "LIFE GIBBS AND TAKE",
                "wins": 6,
                "losses": 2,
                "ties": 0,
                "points_for": 878.52,
                "points_against": 743.44,
            },
            {
                "team_id": 4,
                "name": "Kamara Sutra",
                "wins": 3,
                "losses": 5,
                "ties": 0,
                "points_for": 830.02,
                "points_against": 957.34,
            },
        ],
        "matchups": [
            {
                "week": 8,
                "home_team_id": 1,
                "away_team_id": 4,
                "home_score": 144.80,
                "away_score": 122.28,
            },
        ],
    }


def test_current_league_endpoint_preserves_empty_collections() -> None:
    snapshot = LeagueSnapshot(
        league_id=529250970,
        name="Jimmy and Friends",
        season=2026,
        current_week=1,
        regular_season_weeks=12,
        teams=(),
        matchups=(),
    )

    app.dependency_overrides[get_current_league_snapshot] = lambda: snapshot

    try:
        response = client.get("/api/leagues/current")
    finally:
        app.dependency_overrides.pop(
            get_current_league_snapshot,
            None,
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["teams"] == []
    assert response.json()["matchups"] == []


def test_current_league_endpoint_returns_upstream_error() -> None:
    def raise_upstream_error() -> LeagueSnapshot:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to retrieve the current ESPN league.",
        )

    app.dependency_overrides[get_current_league_snapshot] = raise_upstream_error

    try:
        response = client.get("/api/leagues/current")
    finally:
        app.dependency_overrides.pop(
            get_current_league_snapshot,
            None,
        )

    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert response.json() == {"detail": "Unable to retrieve the current ESPN league."}
