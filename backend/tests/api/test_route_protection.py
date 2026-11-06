from fastapi import status
from fastapi.testclient import TestClient

from app.api.auth_dependencies import get_current_user
from app.main import app

client = TestClient(app)


def test_health_endpoint_remains_public() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    response = client.get("/health")

    assert response.status_code == status.HTTP_200_OK


def test_csrf_endpoint_remains_public() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    response = client.get("/api/auth/csrf")

    assert response.status_code == status.HTTP_200_OK


def test_league_endpoint_requires_authentication() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    response = client.get("/api/leagues/current")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": "Authentication is required."}


def test_all_play_requires_authentication() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    response = client.get("/api/analytics/all-play")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_schedule_swap_requires_authentication() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    response = client.get(
        "/api/analytics/schedule-swap",
        params={
            "first_team_id": 1,
            "second_team_id": 2,
        },
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_league_average_requires_authentication() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    response = client.get("/api/analytics/league-average")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_optimal_lineups_require_authentication() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    response = client.get("/api/analytics/optimal-lineups")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
