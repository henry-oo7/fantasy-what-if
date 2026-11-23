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


def test_every_protected_read_rejects_unauthenticated_requests() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    requests = (
        (
            "/api/leagues/seasons",
            None,
        ),
        (
            "/api/analytics/optimal-lineups/1",
            None,
        ),
        (
            "/api/admin/sync-status",
            None,
        ),
        (
            "/api/admin/spectator",
            None,
        ),
    )

    for path, params in requests:
        response = client.get(
            path,
            params=params,
        )

        assert response.status_code == (status.HTTP_401_UNAUTHORIZED), path
        assert response.json() == {
            "detail": "Authentication is required.",
        }, path


def test_all_analytics_reads_reject_unauthenticated_requests() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    requests = (
        (
            "/api/analytics/all-play",
            None,
        ),
        (
            "/api/analytics/schedule-swap",
            {
                "first_team_id": 1,
                "second_team_id": 2,
            },
        ),
        (
            "/api/analytics/league-average",
            None,
        ),
        (
            "/api/analytics/optimal-lineups",
            None,
        ),
        (
            "/api/analytics/optimal-lineups/1",
            None,
        ),
    )

    for path, params in requests:
        response = client.get(
            path,
            params=params,
        )

        assert response.status_code == (status.HTTP_401_UNAUTHORIZED), path


def test_all_league_routes_reject_unauthenticated_requests() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    current_response = client.get("/api/leagues/current")
    seasons_response = client.get("/api/leagues/seasons")
    import_response = client.post(
        "/api/leagues/seasons/import",
        json={
            "year": 2025,
        },
    )

    for response in (
        current_response,
        seasons_response,
        import_response,
    ):
        assert response.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert response.json() == {
            "detail": "Authentication is required.",
        }


def test_all_admin_routes_reject_unauthenticated_requests() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    requests = (
        (
            "GET",
            "/api/admin/sync-status",
            None,
        ),
        (
            "POST",
            "/api/admin/sync",
            {
                "full_sync": False,
            },
        ),
        (
            "GET",
            "/api/admin/spectator",
            None,
        ),
        (
            "POST",
            "/api/admin/spectator",
            {
                "password": ("correct horse battery staple"),
            },
        ),
        (
            "POST",
            "/api/admin/spectator/reset-password",
            {
                "password": ("another correct horse battery staple"),
            },
        ),
        (
            "PATCH",
            "/api/admin/spectator/access",
            {
                "is_active": False,
            },
        ),
    )

    for method, path, payload in requests:
        response = client.request(
            method,
            path,
            json=payload,
        )

        assert response.status_code == (status.HTTP_401_UNAUTHORIZED), path
        assert response.json() == {
            "detail": "Authentication is required.",
        }, path
