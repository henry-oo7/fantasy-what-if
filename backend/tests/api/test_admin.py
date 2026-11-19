from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient

from app.api.admin_dependencies import (
    get_synchronization_job_enqueuer,
)
from app.api.auth_dependencies import (
    get_current_user,
    require_session_csrf,
)
from app.db import model_registry  # noqa: F401
from app.db.auth_models import User, UserRole
from app.db.platform_enums import (
    BackgroundJobStatus,
)
from app.main import app
from app.services.admin_synchronization_jobs import (
    AdministratorSynchronizationJob,
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
        workspace_id=1,
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=role,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def fake_job_enqueuer(
    *,
    workspace_id: int,
    full_sync: bool,
) -> AdministratorSynchronizationJob:
    assert workspace_id == 1

    return AdministratorSynchronizationJob(
        job_id=84 if full_sync else 42,
        status=BackgroundJobStatus.PENDING,
        existing=False,
    )


def override_admin() -> User:
    return create_user(role=UserRole.ADMIN)


def override_member() -> User:
    return create_user(role=UserRole.SPECTATOR)


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

    assert payload["provider"] == "espn"
    assert payload["season_id"] == 1
    assert payload["provider_league_id"] == "529250970"
    assert payload["provider_season_id"] == "2026"
    assert payload["latest_run"] is not None
    assert payload["latest_run"]["status"] == "succeeded"


def test_admin_can_enqueue_incremental_sync() -> None:
    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[get_synchronization_job_enqueuer] = lambda: fake_job_enqueuer

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
            get_synchronization_job_enqueuer,
            None,
        )

    assert response.status_code == (status.HTTP_202_ACCEPTED)
    assert response.json() == {
        "job_id": 42,
        "status": "pending",
        "existing": False,
    }


def test_admin_can_enqueue_full_sync() -> None:
    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[get_synchronization_job_enqueuer] = lambda: fake_job_enqueuer

    try:
        response = client.post(
            "/api/admin/sync",
            headers={
                "Origin": "http://localhost:5173",
            },
            json={
                "full_sync": True,
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
            get_synchronization_job_enqueuer,
            None,
        )

    assert response.status_code == (status.HTTP_202_ACCEPTED)
    assert response.json() == {
        "job_id": 84,
        "status": "pending",
        "existing": False,
    }


def test_admin_sync_returns_existing_active_job() -> None:
    def existing_job(
        *,
        workspace_id: int,
        full_sync: bool,
    ) -> AdministratorSynchronizationJob:
        assert workspace_id == 1
        assert full_sync is False

        return AdministratorSynchronizationJob(
            job_id=42,
            status=BackgroundJobStatus.RUNNING,
            existing=True,
        )

    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[get_synchronization_job_enqueuer] = lambda: existing_job

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
            get_synchronization_job_enqueuer,
            None,
        )

    assert response.status_code == (status.HTTP_202_ACCEPTED)
    assert response.json() == {
        "job_id": 42,
        "status": "running",
        "existing": True,
    }


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


def test_admin_can_read_standings_tiebreaker_configuration() -> None:
    app.dependency_overrides[get_current_user] = override_admin

    try:
        response = client.get(
            "/api/admin/standings-tiebreaker",
        )
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["season_id"] == 1
    assert payload["provider_rule"] == "TOTAL_POINTS_SCORED"
    assert payload["provider_rule_supported"] is True
    assert payload["effective_rule"] == "TOTAL_POINTS_SCORED"
    assert payload["effective_rule_source"] == "provider"


def test_admin_can_store_standings_tiebreaker_fallback() -> None:
    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[require_session_csrf] = override_csrf

    try:
        response = client.patch(
            "/api/admin/standings-tiebreaker",
            headers={
                "Origin": "http://localhost:5173",
            },
            json={
                "fallback_rule": "H2H_RECORD",
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

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["provider_rule"] == "TOTAL_POINTS_SCORED"
    assert payload["admin_fallback_rule"] == "H2H_RECORD"
    assert payload["effective_rule"] == "TOTAL_POINTS_SCORED"
    assert payload["effective_rule_source"] == "provider"


def test_standings_tiebreaker_fallback_rejects_unknown_rule() -> None:
    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[require_session_csrf] = override_csrf

    try:
        response = client.patch(
            "/api/admin/standings-tiebreaker",
            headers={
                "Origin": "http://localhost:5173",
            },
            json={
                "fallback_rule": "COIN_FLIP",
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

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"] == ("Fallback rule must be H2H_RECORD or TOTAL_POINTS_SCORED.")


def test_spectator_cannot_read_standings_tiebreaker_configuration() -> None:
    app.dependency_overrides[get_current_user] = override_member

    try:
        response = client.get(
            "/api/admin/standings-tiebreaker",
        )
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def fake_job_status_reader(
    *,
    job_id: int,
    workspace_id: int,
):
    from app.db.platform_enums import (
        BackgroundJobType,
    )
    from app.services.background_job_status import (
        BackgroundJobStatusSnapshot,
    )

    assert workspace_id == 1

    if job_id != 42:
        return None

    timestamp = datetime(
        2026,
        11,
        19,
        18,
        0,
        tzinfo=UTC,
    )

    return BackgroundJobStatusSnapshot(
        job_id=42,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        status=BackgroundJobStatus.RUNNING,
        attempt_count=1,
        maximum_attempts=5,
        progress={
            "stage": "fetching_matchups",
        },
        available_at=timestamp,
        started_at=timestamp,
        completed_at=None,
        failed_at=None,
        canceled_at=None,
        safe_error_code=None,
        safe_error_message=None,
        retryable=None,
    )


def test_admin_can_read_workspace_job_status() -> None:
    from app.api.admin_dependencies import (
        get_background_job_status_reader,
    )

    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[get_background_job_status_reader] = lambda: fake_job_status_reader

    try:
        response = client.get("/api/admin/jobs/42")
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        app.dependency_overrides.pop(
            get_background_job_status_reader,
            None,
        )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["job_id"] == 42
    assert payload["job_type"] == ("incremental_season_sync")
    assert payload["status"] == "running"
    assert payload["attempt_count"] == 1
    assert payload["maximum_attempts"] == 5
    assert payload["progress"] == {
        "stage": "fetching_matchups",
    }
    assert payload["completed_at"] is None
    assert payload["safe_error_message"] is None

    assert "payload" not in payload
    assert "idempotency_key" not in payload
    assert "claimed_by" not in payload
    assert "claimed_at" not in payload
    assert "lease_expires_at" not in payload


def test_unknown_or_other_workspace_job_returns_404() -> None:
    from app.api.admin_dependencies import (
        get_background_job_status_reader,
    )

    app.dependency_overrides[get_current_user] = override_admin
    app.dependency_overrides[get_background_job_status_reader] = lambda: fake_job_status_reader

    try:
        response = client.get("/api/admin/jobs/999")
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        app.dependency_overrides.pop(
            get_background_job_status_reader,
            None,
        )

    assert response.status_code == (status.HTTP_404_NOT_FOUND)
    assert response.json() == {
        "detail": "Background job was not found.",
    }


def test_member_cannot_read_job_status() -> None:
    app.dependency_overrides[get_current_user] = override_member

    try:
        response = client.get("/api/admin/jobs/42")
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

    assert response.status_code == (status.HTTP_403_FORBIDDEN)


def test_invalid_job_id_is_rejected() -> None:
    app.dependency_overrides[get_current_user] = override_admin

    try:
        response = client.get("/api/admin/jobs/0")
    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
