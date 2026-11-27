# ruff: noqa: F811  (imported pytest fixtures are used as test parameters)
"""Routine behaviour of the personal League and Season routes (ADR 0010).

Real sessions through the API with synthetic committed data. Authorization
invariants live in the critical module; this covers shapes, ordering,
validation, compatibility with the workspace routes and response headers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models import OptimalLineupResult, ScheduleRule
from app.db.platform_enums import UserSeasonAccessStatus
from app.db.session import SessionFactory
from app.main import app
from tests.support.league_import import (  # noqa: F401 - fixtures
    Registry,
    create_caller,
    forbid_live_network,
    registry,
)
from tests.support.league_season_access import (  # noqa: F401 - fixtures
    PREFERENCE_ENDPOINT,
    SeededSeason,
    get,
    grant,
    put_default,
    real_workspace_authentication,
    season_paths,
    seed_season,
    set_default,
    stored_default,
)

ACTIVE = UserSeasonAccessStatus.ACTIVE
REVERIFY = UserSeasonAccessStatus.REVERIFICATION_REQUIRED
REVOKED = UserSeasonAccessStatus.REVOKED
NOT_FOUND = {"detail": "League season not found."}
SEASON_KEYS = {
    "season_id",
    "season_year",
    "access_status",
    "sync_state",
    "data_available",
    "last_successful_sync_at",
}
DETAIL_KEYS = SEASON_KEYS | {"provider", "provider_league_id", "league_name"}
LEAGUE_KEYS = {"provider", "provider_league_id", "league_name", "seasons"}
SYNCED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def delete_rows(model: type, season_id: int) -> None:
    with SessionFactory() as session:
        session.execute(delete(model).where(model.season_id == season_id))
        session.commit()


def granted_season(registry: Registry, caller, **kwargs: Any) -> SeededSeason:
    season = seed_season(registry, **kwargs)
    grant(caller.user_id, season.season_id)
    return season


# 1. Listing grouping and order


def test_listing_groups_orders_and_filters_by_status(registry: Registry) -> None:
    user = create_caller(registry)
    bravo_id, alpha_id = "770000101", "770000102"
    bravo = seed_season(
        registry, provider_league_id=bravo_id, year=2024, league_name="Bravo League"
    )
    alpha_old = seed_season(
        registry, provider_league_id=alpha_id, year=2023, league_name="Alpha League"
    )
    alpha_new = seed_season(registry, provider_league_id=alpha_id, year=2024)
    revoked = seed_season(registry, provider_league_id=bravo_id, year=2022)
    for season in (bravo, alpha_old, alpha_new):
        grant(user.user_id, season.season_id)
    # Bravo 2022 is revoked and must be omitted; Alpha 2021 needs reverification.
    grant(user.user_id, revoked.season_id, REVOKED)
    reverify = seed_season(registry, provider_league_id=alpha_id, year=2021)
    grant(user.user_id, reverify.season_id, REVERIFY)

    response = get(user, "/api/leagues")

    assert response.status_code == 200
    leagues = response.json()["leagues"]
    assert [league["league_name"] for league in leagues] == ["Alpha League", "Bravo League"]
    alpha, bravo_league = leagues
    assert [s["season_year"] for s in alpha["seasons"]] == [2024, 2023, 2021]
    assert [s["season_id"] for s in alpha["seasons"]] == [
        alpha_new.season_id,
        alpha_old.season_id,
        reverify.season_id,
    ]
    assert [s["access_status"] for s in alpha["seasons"]] == [
        "active",
        "active",
        "reverification_required",
    ]
    assert [s["season_id"] for s in bravo_league["seasons"]] == [bravo.season_id]
    listed = {s["season_id"] for league in leagues for s in league["seasons"]}
    assert revoked.season_id not in listed


# 2. Field allowlists


def test_response_field_allowlists(registry: Registry) -> None:
    user = create_caller(registry)
    season = granted_season(registry, user)

    listing = get(user, "/api/leagues").json()
    assert set(listing) == {"default_season_id", "leagues"}
    (league,) = listing["leagues"]
    assert set(league) == LEAGUE_KEYS
    assert league["provider"] == "espn"
    assert league["provider_league_id"] == season.provider_league_id
    (item,) = league["seasons"]
    assert set(item) == SEASON_KEYS

    detail = get(user, f"/api/seasons/{season.season_id}")
    assert detail.status_code == 200
    assert set(detail.json()) == DETAIL_KEYS
    assert detail.json()["provider"] == "espn"
    assert detail.json()["season_id"] == season.season_id


# 3. last_successful_sync_at


@pytest.mark.parametrize("data", [True, False])
def test_last_successful_sync_at(registry: Registry, data: bool) -> None:
    user = create_caller(registry)
    season = granted_season(registry, user, data=data)

    listed = get(user, "/api/leagues").json()["leagues"][0]["seasons"][0]
    detail = get(user, f"/api/seasons/{season.season_id}").json()

    for body in (listed, detail):
        assert body["data_available"] is data
        if data:
            assert parse(body["last_successful_sync_at"]) == SYNCED
        else:
            assert body["last_successful_sync_at"] is None


# 4. Default preference


def test_default_is_null_without_preference(registry: Registry) -> None:
    user = create_caller(registry)
    granted_season(registry, user)

    assert get(user, "/api/leagues").json()["default_season_id"] is None


@pytest.mark.parametrize("status", [ACTIVE, REVERIFY])
def test_readable_default_is_returned(registry: Registry, status) -> None:
    user = create_caller(registry)
    season = seed_season(registry)
    grant(user.user_id, season.season_id, status)
    set_default(user.user_id, season.season_id)

    assert get(user, "/api/leagues").json()["default_season_id"] == season.season_id


def test_put_reverification_required_season_is_accepted(registry: Registry) -> None:
    user = create_caller(registry)
    season = seed_season(registry)
    grant(user.user_id, season.season_id, REVERIFY)

    response = put_default(user, {"season_id": season.season_id})

    assert response.status_code == 200
    assert response.json() == {"default_season_id": season.season_id}
    assert stored_default(user.user_id) == season.season_id


def test_put_is_idempotent_and_second_replaces_first(registry: Registry) -> None:
    user = create_caller(registry)
    first = granted_season(registry, user)
    second = granted_season(registry, user)

    for _ in range(2):
        response = put_default(user, {"season_id": first.season_id})
        assert response.status_code == 200
        assert response.json() == {"default_season_id": first.season_id}
        assert stored_default(user.user_id) == first.season_id

    response = put_default(user, {"season_id": second.season_id})
    assert response.status_code == 200
    assert stored_default(user.user_id) == second.season_id
    assert get(user, "/api/leagues").json()["default_season_id"] == second.season_id


@pytest.mark.parametrize(
    "body",
    [
        {"season_id": 0},
        {"season_id": -1},
        {"season_id": "abc"},
        {},
        {"season_id": "VALID", "extra": 1},
    ],
)
def test_invalid_put_bodies_are_422_and_leave_default_unchanged(
    registry: Registry, body: dict[str, Any]
) -> None:
    user = create_caller(registry)
    season = granted_season(registry, user)
    set_default(user.user_id, season.season_id)
    body = {k: season.season_id if v == "VALID" else v for k, v in body.items()}

    response = put_default(user, body)

    assert response.status_code == 422
    assert stored_default(user.user_id) == season.season_id


# 5. Analytics compatibility with the workspace routes


def test_personal_analytics_match_workspace_routes(registry: Registry) -> None:
    admin = create_caller(registry)
    season = seed_season(registry, workspace_id=admin.workspace_id)
    grant(admin.user_id, season.season_id)
    first, second = season.team_ids
    sid = season.season_id
    base = f"/api/seasons/{sid}"
    pairs: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = [
        ("/api/leagues/current", {}, f"{base}/snapshot", {}),
        (
            "/api/leagues/current/playoff-predictor",
            {},
            f"{base}/playoff-predictor",
            {},
        ),
        ("/api/analytics/all-play", {}, f"{base}/analytics/all-play", {}),
        ("/api/analytics/league-average", {}, f"{base}/analytics/league-average", {}),
        ("/api/analytics/optimal-lineups", {}, f"{base}/analytics/optimal-lineups", {}),
        (
            f"/api/analytics/optimal-lineups/{first}",
            {},
            f"{base}/analytics/optimal-lineups/{first}",
            {},
        ),
        (
            "/api/analytics/schedule-swap",
            {"first_team_id": first, "second_team_id": second},
            f"{base}/analytics/schedule-swap",
            {"first_team_id": first, "second_team_id": second},
        ),
    ]

    for workspace_path, workspace_params, personal_path, personal_params in pairs:
        workspace = get(admin, workspace_path, season_id=sid, **workspace_params)
        personal = get(admin, personal_path, **personal_params)
        assert workspace.status_code == 200, workspace_path
        assert personal.status_code == 200, personal_path
        assert personal.json() == workspace.json(), personal_path


# 6. Route validation


@pytest.mark.parametrize("raw", ["abc", "0"])
def test_invalid_season_path_is_422(registry: Registry, raw: str) -> None:
    user = create_caller(registry)

    assert get(user, f"/api/seasons/{raw}").status_code == 422


def test_zero_season_id_body_is_identical_with_and_without_access(registry: Registry) -> None:
    holder = create_caller(registry)
    stranger = create_caller(registry)
    granted_season(registry, holder)

    first = get(holder, "/api/seasons/0")
    second = get(stranger, "/api/seasons/0")

    assert first.status_code == second.status_code == 422
    assert first.json() == second.json()


@pytest.mark.parametrize("missing", ["first_team_id", "second_team_id"])
def test_schedule_swap_requires_both_teams(registry: Registry, missing: str) -> None:
    user = create_caller(registry)
    season = granted_season(registry, user)
    params = {"first_team_id": season.team_ids[0], "second_team_id": season.team_ids[1]}
    del params[missing]

    response = get(user, f"/api/seasons/{season.season_id}/analytics/schedule-swap", **params)

    assert response.status_code == 422


def test_schedule_swap_same_team_matches_workspace_detail(registry: Registry) -> None:
    admin = create_caller(registry)
    season = seed_season(registry, workspace_id=admin.workspace_id)
    grant(admin.user_id, season.season_id)
    team = season.team_ids[0]
    params = {"first_team_id": team, "second_team_id": team}

    personal = get(admin, f"/api/seasons/{season.season_id}/analytics/schedule-swap", **params)
    workspace = get(admin, "/api/analytics/schedule-swap", season_id=season.season_id, **params)

    assert personal.status_code == workspace.status_code == 422
    assert personal.json()["detail"] == workspace.json()["detail"]


# 7. Playoff predictor configuration


def test_playoff_predictor_missing_schedule_rule_is_409_like_workspace(registry: Registry) -> None:
    admin = create_caller(registry)
    season = seed_season(registry, workspace_id=admin.workspace_id)
    grant(admin.user_id, season.season_id)
    delete_rows(ScheduleRule, season.season_id)

    personal = get(admin, f"/api/seasons/{season.season_id}/playoff-predictor")
    workspace = get(admin, "/api/leagues/current/playoff-predictor", season_id=season.season_id)

    assert workspace.status_code == 409
    assert personal.status_code == 409
    assert personal.json()["detail"] == workspace.json()["detail"]


# 8. Optimal lineups


def test_missing_optimal_lineup_results_is_409(registry: Registry) -> None:
    user = create_caller(registry)
    season = granted_season(registry, user)
    delete_rows(OptimalLineupResult, season.season_id)

    response = get(user, f"/api/seasons/{season.season_id}/analytics/optimal-lineups")

    assert response.status_code == 409
    assert response.json() == {"detail": "Optimal-lineup analysis has not been generated yet."}


# 9. Headers


def assert_no_store(response) -> None:
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("pragma") == "no-cache"


def test_personal_routes_are_no_store(registry: Registry) -> None:
    user = create_caller(registry)
    season = granted_season(registry, user)

    for path, params in season_paths(season):
        response = get(user, path, **params)
        assert response.status_code == 200, path
        assert_no_store(response)

    assert_no_store(get(user, "/api/leagues"))
    assert_no_store(put_default(user, {"season_id": season.season_id}))
    assert_no_store(put_default(user, {"season_id": 0}))


def test_not_found_and_conflict_responses_are_no_store(registry: Registry) -> None:
    user = create_caller(registry)
    other = seed_season(registry)
    missing = granted_season(registry, user)
    delete_rows(OptimalLineupResult, missing.season_id)

    not_found = get(user, f"/api/seasons/{other.season_id}")
    conflict = get(user, f"/api/seasons/{missing.season_id}/analytics/optimal-lineups")

    assert not_found.status_code == 404
    assert not_found.json() == NOT_FOUND
    assert conflict.status_code == 409
    assert_no_store(not_found)
    assert_no_store(conflict)


def test_workspace_route_keeps_existing_cache_behaviour(registry: Registry) -> None:
    admin = create_caller(registry)
    season = seed_season(registry, workspace_id=admin.workspace_id)

    response = get(admin, "/api/leagues/current", season_id=season.season_id)

    assert response.status_code == 200
    assert response.headers.get("cache-control") != "no-store"


# 10. OpenAPI


def test_openapi_lists_personal_routes() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    expected = {
        "/api/leagues": "get",
        "/api/seasons/{season_id}": "get",
        "/api/seasons/{season_id}/snapshot": "get",
        "/api/seasons/{season_id}/playoff-predictor": "get",
        "/api/seasons/{season_id}/analytics/all-play": "get",
        "/api/seasons/{season_id}/analytics/league-average": "get",
        "/api/seasons/{season_id}/analytics/schedule-swap": "get",
        "/api/seasons/{season_id}/analytics/optimal-lineups": "get",
        "/api/seasons/{season_id}/analytics/optimal-lineups/{team_id}": "get",
        PREFERENCE_ENDPOINT: "put",
    }

    for path, method in expected.items():
        assert method in paths[path], path
