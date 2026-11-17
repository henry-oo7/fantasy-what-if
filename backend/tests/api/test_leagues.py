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


def test_authenticated_user_can_list_imported_seasons() -> None:
    response = client.get("/api/leagues/seasons")

    assert response.status_code == 200

    payload = response.json()

    assert payload["seasons"]

    active = [season for season in payload["seasons"] if season["is_active"]]

    assert len(active) == 1
    assert active[0]["season_id"] > 0
    assert active[0]["league_id"] > 0
    assert active[0]["league_name"]
    assert active[0]["year"] > 2000


def test_playoff_predictor_endpoint_returns_snapshot() -> None:
    from app.api.dependencies import (
        get_playoff_predictor_snapshot,
    )
    from app.domain.playoff_predictor import (
        PlayoffPredictorMatchup,
        PlayoffPredictorSnapshot,
        PlayoffPredictorTeam,
    )

    snapshot = PlayoffPredictorSnapshot(
        league_id=1,
        league_name="Jimmy and Friends",
        season_id=9,
        season=2026,
        current_week=9,
        regular_season_weeks=12,
        playoff_team_count=6,
        playoff_seed_tie_rule="H2H_RECORD",
        playoff_seed_tie_rule_source="provider",
        allows_ties=True,
        teams=(
            PlayoffPredictorTeam(
                team_id=1,
                name="Team Alpha",
                wins=6,
                losses=2,
                ties=0,
                points_for=900.5,
                points_against=810.25,
            ),
            PlayoffPredictorTeam(
                team_id=2,
                name="Team Bravo",
                wins=5,
                losses=3,
                ties=0,
                points_for=875.0,
                points_against=820.0,
            ),
        ),
        completed_matchups=(
            PlayoffPredictorMatchup(
                matchup_id=17,
                week=8,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=105.0,
                is_complete=True,
            ),
        ),
        remaining_matchups=(
            PlayoffPredictorMatchup(
                matchup_id=23,
                week=9,
                home_team_id=2,
                away_team_id=1,
                home_score=None,
                away_score=None,
                is_complete=False,
            ),
        ),
    )

    app.dependency_overrides[get_playoff_predictor_snapshot] = lambda: snapshot

    try:
        response = client.get("/api/leagues/current/playoff-predictor")
    finally:
        app.dependency_overrides.pop(
            get_playoff_predictor_snapshot,
            None,
        )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["league_id"] == 1
    assert payload["season_id"] == 9
    assert payload["playoff_team_count"] == 6
    assert payload["playoff_seed_tie_rule"] == "H2H_RECORD"
    assert payload["playoff_seed_tie_rule_source"] == "provider"
    assert payload["allows_ties"] is True
    assert payload["teams"][0]["name"] == "Team Alpha"
    assert payload["completed_matchups"][0]["matchup_id"] == 17
    assert payload["remaining_matchups"][0] == {
        "matchup_id": 23,
        "week": 9,
        "home_team_id": 2,
        "away_team_id": 1,
        "home_score": None,
        "away_score": None,
        "is_complete": False,
    }


def test_playoff_predictor_endpoint_preserves_configuration_error() -> None:
    from app.api.dependencies import (
        get_playoff_predictor_snapshot,
    )

    def unavailable():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Playoff team count is unavailable for the selected season."),
        )

    app.dependency_overrides[get_playoff_predictor_snapshot] = unavailable

    try:
        response = client.get("/api/leagues/current/playoff-predictor")
    finally:
        app.dependency_overrides.pop(
            get_playoff_predictor_snapshot,
            None,
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": ("Playoff team count is unavailable for the selected season.")
    }
