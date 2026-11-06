from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient

from app.api.admin_dependencies import (
    get_synchronization_runner,
)
from app.api.auth_dependencies import (
    get_current_user,
    require_session_csrf,
)
from app.db.auth_models import User, UserRole
from app.main import app
from app.services.espn_sync import SyncResult
from app.services.sync_lock import (
    SyncAlreadyRunningError,
)

client = TestClient(app)


def create_user(
    *,
    role: UserRole,
) -> User:
    return User(
        id=123_456,
        username=f"{role.value}-test-user",
        display_name="Admin API Test User",
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=role,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def fake_sync_result(
    *,
    full_sync: bool,
) -> SyncResult:
    return SyncResult(
        league_id=529250970,
        season=2026,
        current_week=9,
        full_sync=full_sync,
        player_weeks=(7, 8),
        teams_upserted=12,
        matchups_upserted=72,
        fantasy_byes_upserted=0,
        player_week_scores_upserted=400,
    )


def override_admin() -> User:
    return create_user(role=UserRole.ADMIN)


def override_member() -> User:
    return create_user(role=UserRole.MEMBER)


def override_csrf() -> None:
    return None


def test_sync_status_requires_admin() -> None:
    app.dependency_overrides[get_current_user] = override_member

    try:
        response = client.get("/api/admin/sync-status")
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_admin_can_read_sync_status() -> None:
    app.dependency_overrides[get_current_user] = override_admin

    try:
        response = client.get("/api/admin/sync-status")
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["league_id"] == 529250970
    assert payload["season"] == 2026
    assert payload["latest_run"] is not None
    assert payload["latest_run"]["status"] == "succeeded"


def test_admin_can_trigger_incremental_sync() -> None:
    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[get_synchronization_runner] = lambda: fake_sync_result

    try:
        response = client.post(
            "/api/admin/sync",
            headers={
                "Origin": "http://localhost:5173",
            },
            json={
                "full_sync": False,
            },
        )
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        app.dependency_overrides.pop(
            require_session_csrf,
            None,
        )
        app.dependency_overrides.pop(
            get_synchronization_runner,
            None,
        )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["full_sync"] is False
    assert payload["player_weeks"] == [7, 8]
    assert payload["teams_upserted"] == 12


def test_member_cannot_trigger_sync() -> None:
    app.dependency_overrides[get_current_user] = override_member
    app.dependency_overrides[require_session_csrf] = override_csrf

    try:
        response = client.post(
            "/api/admin/sync",
            headers={
                "Origin": "http://localhost:5173",
            },
            json={
                "full_sync": False,
            },
        )
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        app.dependency_overrides.pop(
            require_session_csrf,
            None,
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_sync_conflict_returns_409() -> None:
    def locked_runner(
        *,
        full_sync: bool,
    ) -> SyncResult:
        del full_sync

        raise SyncAlreadyRunningError(
            "Another synchronization is already running for this league and season."
        )

    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[get_synchronization_runner] = lambda: locked_runner

    try:
        response = client.post(
            "/api/admin/sync",
            headers={
                "Origin": "http://localhost:5173",
            },
            json={
                "full_sync": False,
            },
        )
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        app.dependency_overrides.pop(
            require_session_csrf,
            None,
        )
        app.dependency_overrides.pop(
            get_synchronization_runner,
            None,
        )

    assert response.status_code == status.HTTP_409_CONFLICT
