from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import status
from fastapi.testclient import TestClient

from app.api.admin_dependencies import (
    get_season_importer,
)
from app.api.auth_dependencies import (
    get_current_user,
    require_session_csrf,
)
from app.db.auth_models import User, UserRole
from app.db.platform_enums import SeasonStatus
from app.main import app
from app.services.season_import import (
    SeasonImportConflictError,
    SeasonImportResult,
    SeasonImportUnavailableError,
    SeasonImportValidationError,
)

client = TestClient(app)
TRUSTED_ORIGIN = "http://localhost:5173"


def user(
    *,
    role: UserRole,
) -> User:
    return User(
        id=500,
        username=f"{role.value}-season-import",
        display_name="Season Import Test",
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=role,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def admin_user() -> User:
    return user(role=UserRole.ADMIN)


def spectator_user() -> User:
    return user(role=UserRole.SPECTATOR)


def allow_csrf() -> None:
    return None


def import_result(
    *,
    year: int = 2025,
    season_status: SeasonStatus = (SeasonStatus.COMPLETED),
    synchronization_enabled: bool = False,
) -> SeasonImportResult:
    return SeasonImportResult(
        installation_id=1,
        league_id=10,
        season_id=25,
        year=year,
        status=season_status,
        synchronization_enabled=(synchronization_enabled),
        sync_result=SimpleNamespace(
            teams_upserted=12,
            matchups_upserted=84,
            fantasy_byes_upserted=0,
            player_week_scores_upserted=1344,
        ),
    )


def apply_overrides(
    importer,
    *,
    current_user=admin_user,
) -> None:
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[require_session_csrf] = allow_csrf
    app.dependency_overrides[get_season_importer] = lambda: importer


def clear_overrides() -> None:
    for dependency in (
        get_current_user,
        require_session_csrf,
        get_season_importer,
    ):
        app.dependency_overrides.pop(
            dependency,
            None,
        )


def test_admin_can_import_historical_season() -> None:
    captured = {}

    def importer(
        *,
        season_year: int,
    ) -> SeasonImportResult:
        captured["season_year"] = season_year
        return import_result(year=season_year)

    apply_overrides(importer)

    try:
        response = client.post(
            "/api/leagues/seasons/import",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json={
                "year": 2025,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert captured == {
        "season_year": 2025,
    }
    assert response.json() == {
        "season_id": 25,
        "league_id": 10,
        "year": 2025,
        "status": "completed",
        "synchronization_enabled": False,
        "teams_upserted": 12,
        "matchups_upserted": 84,
        "fantasy_byes_upserted": 0,
        "player_week_scores_upserted": 1344,
    }


def test_admin_can_import_current_season() -> None:
    def importer(
        *,
        season_year: int,
    ) -> SeasonImportResult:
        return import_result(
            year=season_year,
            season_status=SeasonStatus.ACTIVE,
            synchronization_enabled=True,
        )

    apply_overrides(importer)

    try:
        response = client.post(
            "/api/leagues/seasons/import",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json={
                "year": 2026,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "active"
    assert response.json()["synchronization_enabled"] is True


def test_spectator_cannot_import_season() -> None:
    apply_overrides(
        lambda **_: import_result(),
        current_user=spectator_user,
    )

    try:
        response = client.post(
            "/api/leagues/seasons/import",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json={
                "year": 2025,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "Administrator access is required.",
    }


def test_duplicate_year_returns_conflict() -> None:
    def importer(**_):
        raise SeasonImportConflictError("Season 2025 is already imported.")

    apply_overrides(importer)

    try:
        response = client.post(
            "/api/leagues/seasons/import",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json={
                "year": 2025,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "Season 2025 is already imported.",
    }


def test_unavailable_import_returns_conflict() -> None:
    def importer(**_):
        raise SeasonImportUnavailableError("Exactly one installed fantasy league is required.")

    apply_overrides(importer)

    try:
        response = client.post(
            "/api/leagues/seasons/import",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json={
                "year": 2025,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_409_CONFLICT


def test_future_year_returns_validation_error() -> None:
    def importer(**_):
        raise SeasonImportValidationError(
            "Season 2027 cannot be imported before the 2027 calendar year."
        )

    apply_overrides(importer)

    try:
        response = client.post(
            "/api/leagues/seasons/import",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json={
                "year": 2027,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "2027" in response.json()["detail"]


def test_provider_failure_returns_safe_error() -> None:
    def importer(**_):
        raise RuntimeError("private-cookie {private-swid}")

    apply_overrides(importer)

    try:
        response = client.post(
            "/api/leagues/seasons/import",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json={
                "year": 2025,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert response.json() == {
        "detail": ("Unable to import the requested fantasy season."),
    }
    assert "private-cookie" not in response.text
    assert "private-swid" not in response.text
