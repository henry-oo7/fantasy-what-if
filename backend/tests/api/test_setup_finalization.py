from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient

from app.api.auth_dependencies import (
    get_current_user,
    require_session_csrf,
)
from app.api.setup_dependencies import (
    get_setup_finalizer,
)
from app.db.auth_models import User, UserRole
from app.db.platform_enums import FantasyProvider
from app.main import app
from app.services.provider_sync import (
    ProviderSyncResult,
)
from app.services.setup_finalization import (
    SetupFinalizationResult,
    SetupFinalizationUnavailableError,
    SetupFinalizationValidationError,
)

client = TestClient(app)
TRUSTED_ORIGIN = "http://localhost:5173"


def user(
    *,
    role: UserRole,
) -> User:
    return User(
        id=100,
        username=f"setup-{role.value}",
        display_name="Setup User",
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=role,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def override_admin() -> User:
    return user(role=UserRole.ADMIN)


def override_spectator() -> User:
    return user(role=UserRole.SPECTATOR)


def override_csrf() -> None:
    return None


def successful_result() -> SetupFinalizationResult:
    return SetupFinalizationResult(
        installation_id=1,
        league_id=8,
        season_id=9,
        sync_result=ProviderSyncResult(
            provider=FantasyProvider.ESPN,
            season_id=9,
            provider_league_id="529250970",
            provider_season_id="2026",
            current_week=9,
            full_sync=True,
            matchup_weeks=tuple(range(1, 13)),
            player_weeks=tuple(range(1, 9)),
            teams_upserted=12,
            matchups_upserted=72,
            fantasy_byes_upserted=0,
            player_week_scores_upserted=1579,
            analytics_rebuilt=False,
        ),
    )


def apply_overrides(
    finalizer,
    *,
    current_user=override_admin,
) -> None:
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[get_setup_finalizer] = lambda: finalizer


def clear_overrides() -> None:
    for dependency in (
        get_current_user,
        require_session_csrf,
        get_setup_finalizer,
    ):
        app.dependency_overrides.pop(
            dependency,
            None,
        )


def test_admin_can_finalize_setup() -> None:
    apply_overrides(successful_result)

    try:
        response = client.post(
            "/api/setup/finalize",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "installation_status": "active",
        "installation_id": 1,
        "league_id": 8,
        "season_id": 9,
        "provider": "espn",
        "provider_league_id": "529250970",
        "provider_season_id": "2026",
        "current_week": 9,
        "full_sync": True,
        "teams_upserted": 12,
        "matchups_upserted": 72,
        "fantasy_byes_upserted": 0,
        "player_week_scores_upserted": 1579,
    }


def test_setup_finalization_requires_admin() -> None:
    apply_overrides(
        successful_result,
        current_user=override_spectator,
    )

    try:
        response = client.post(
            "/api/setup/finalize",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "Administrator access is required.",
    }


def test_unavailable_setup_returns_conflict() -> None:
    def finalizer():
        raise SetupFinalizationUnavailableError(
            "Setup can be finalized only while setup is in progress."
        )

    apply_overrides(finalizer)

    try:
        response = client.post(
            "/api/setup/finalize",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_409_CONFLICT


def test_provider_validation_failure_is_safe() -> None:
    def finalizer():
        raise SetupFinalizationValidationError("ESPN could not load the configured league.")

    apply_overrides(finalizer)

    try:
        response = client.post(
            "/api/setup/finalize",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
    assert response.json() == {
        "detail": ("ESPN could not load the configured league."),
    }


def test_unexpected_sync_failure_is_generic() -> None:
    def finalizer():
        raise RuntimeError("private-cookie must not escape")

    apply_overrides(finalizer)

    try:
        response = client.post(
            "/api/setup/finalize",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == (status.HTTP_502_BAD_GATEWAY)
    assert response.json() == {
        "detail": ("Unable to complete the initial provider synchronization."),
    }
    assert "private-cookie" not in response.text
