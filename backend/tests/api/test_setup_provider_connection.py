from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient

from app.api.auth_dependencies import (
    get_current_user,
    require_session_csrf,
)
from app.api.setup_dependencies import (
    get_setup_provider_configurator,
)
from app.db.auth_models import User, UserRole
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import ProviderConnection
from app.main import app
from app.services.setup_provider_connection import (
    SetupProviderAlreadyConfiguredError,
    SetupProviderValidationError,
)

client = TestClient(app)
TRUSTED_ORIGIN = "http://localhost:5173"


def admin_user() -> User:
    return User(
        id=100,
        username="setup-admin",
        display_name="Setup Administrator",
        email="setup-admin@example.com",
        password_hash="$argon2id$test-placeholder",
        role=UserRole.ADMIN,
        is_active=True,
    )


def spectator_user() -> User:
    return User(
        id=101,
        username="setup-spectator",
        display_name="Setup Spectator",
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=UserRole.SPECTATOR,
        is_active=True,
    )


def override_admin() -> User:
    return admin_user()


def override_spectator() -> User:
    return spectator_user()


def override_csrf() -> None:
    return None


def valid_request() -> dict[str, str]:
    return {
        "provider": "espn",
        "display_name": "ESPN League",
        "league_id": "529250970",
        "season": "2026",
        "espn_s2": "private-cookie",
        "swid": "{private-swid}",
    }


def stored_connection() -> ProviderConnection:
    return ProviderConnection(
        id=17,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        display_name="ESPN League",
        encrypted_credentials="ciphertext",
        credential_version=1,
        is_active=True,
        last_validated_at=datetime.now(UTC),
        last_validation_error=None,
    )


def apply_overrides(
    configurator,
    *,
    user=override_admin,
) -> None:
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[get_setup_provider_configurator] = lambda: configurator


def clear_overrides() -> None:
    for dependency in (
        get_current_user,
        require_session_csrf,
        get_setup_provider_configurator,
    ):
        app.dependency_overrides.pop(
            dependency,
            None,
        )


def test_admin_can_store_valid_provider_metadata() -> None:
    captured = {}

    def configurator(
        *,
        provider,
        display_name,
        credentials,
    ):
        captured["provider"] = provider
        captured["display_name"] = display_name
        captured["credentials"] = dict(credentials.values)
        return stored_connection()

    apply_overrides(configurator)

    try:
        response = client.post(
            "/api/setup/provider-connection",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json=valid_request(),
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "connection_id": 17,
        "provider": "espn",
        "display_name": "ESPN League",
        "credential_version": 1,
        "is_active": True,
        "validated": True,
    }

    assert captured == {
        "provider": FantasyProvider.ESPN,
        "display_name": "ESPN Fantasy Football",
        "credentials": {
            "league_id": "529250970",
            "season": "2026",
            "espn_s2": "private-cookie",
            "swid": "{private-swid}",
        },
    }

    serialized = response.text.casefold()

    assert "private-cookie" not in serialized
    assert "private-swid" not in serialized
    assert "ciphertext" not in serialized
    assert "encrypted_credentials" not in serialized


def test_provider_connection_requires_admin() -> None:
    apply_overrides(
        lambda **_: stored_connection(),
        user=override_spectator,
    )

    try:
        response = client.post(
            "/api/setup/provider-connection",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json=valid_request(),
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "Administrator access is required.",
    }


def test_provider_validation_error_is_safe() -> None:
    def configurator(**_):
        raise SetupProviderValidationError("ESPN could not load the configured league.")

    apply_overrides(configurator)

    try:
        response = client.post(
            "/api/setup/provider-connection",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json=valid_request(),
        )
    finally:
        clear_overrides()

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
    assert response.json() == {
        "detail": ("ESPN could not load the configured league."),
    }
    assert "private-cookie" not in response.text
    assert "{private-swid}" not in response.text


def test_duplicate_provider_connection_returns_conflict() -> None:
    def configurator(**_):
        raise SetupProviderAlreadyConfiguredError("A provider connection is already configured.")

    apply_overrides(configurator)

    try:
        response = client.post(
            "/api/setup/provider-connection",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json=valid_request(),
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_409_CONFLICT


def test_unimplemented_provider_is_rejected_safely() -> None:
    def configurator(**_):
        raise SetupProviderValidationError("The selected fantasy provider is not implemented.")

    apply_overrides(configurator)

    request = valid_request()
    request["provider"] = "sleeper"

    try:
        response = client.post(
            "/api/setup/provider-connection",
            headers={
                "Origin": TRUSTED_ORIGIN,
            },
            json=request,
        )
    finally:
        clear_overrides()

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
    assert response.json() == {
        "detail": ("The selected fantasy provider is not implemented."),
    }
