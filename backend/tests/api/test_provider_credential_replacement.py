from datetime import UTC, datetime

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.admin_dependencies import (
    get_provider_credential_replacer,
)
from app.api.auth_dependencies import (
    get_current_user,
    get_current_workspace_id,
    require_session_csrf,
)
from app.db.auth_models import User, UserRole
from app.main import app
from app.providers.credential_vault import (
    ProviderCredentialConfigurationError,
)
from app.services.provider_credential_replacement import (
    CHANGED_DURING_VALIDATION_MESSAGE,
    ProviderCredentialReplacementResult,
    ProviderCredentialReplacementUnavailableError,
    ProviderCredentialReplacementValidationError,
)

client = TestClient(app)
VALIDATED_AT = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


def user(role: UserRole) -> User:
    return User(
        id=123_456,
        username=f"{role.value}-credential-test",
        display_name="Credential Test User",
        workspace_id=1,
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=role,
        is_active=True,
        created_at=VALIDATED_AT,
        updated_at=VALIDATED_AT,
    )


def override_admin() -> User:
    return user(UserRole.ADMIN)


def override_spectator() -> User:
    return user(UserRole.SPECTATOR)


def override_workspace() -> int:
    return 1


def override_csrf() -> None:
    return None


def successful_replacer(
    *,
    workspace_id: int,
    credentials,
) -> ProviderCredentialReplacementResult:
    assert workspace_id == 1
    assert dict(credentials.values) == {
        "espn_s2": "replacement-cookie",
        "swid": "{replacement-swid}",
    }

    return ProviderCredentialReplacementResult(
        workspace_id=1,
        connection_id=30,
        provider="espn",
        provider_league_id="529250970",
        provider_season_id="2026",
        credential_version=1,
        credential_schema_version=1,
        validated_at=VALIDATED_AT,
    )


def apply_overrides(
    replacer=successful_replacer,
    *,
    current_user=override_admin,
) -> None:
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_current_workspace_id] = override_workspace
    app.dependency_overrides[require_session_csrf] = override_csrf
    app.dependency_overrides[get_provider_credential_replacer] = lambda: replacer


def clear_overrides() -> None:
    for dependency in (
        get_current_user,
        get_current_workspace_id,
        require_session_csrf,
        get_provider_credential_replacer,
    ):
        app.dependency_overrides.pop(
            dependency,
            None,
        )


def request_payload() -> dict[str, str]:
    return {
        "espn_s2": "replacement-cookie",
        "swid": "{replacement-swid}",
    }


def test_admin_can_replace_provider_credentials() -> None:
    apply_overrides()

    try:
        response = client.patch(
            "/api/admin/provider-credentials",
            headers={
                "Origin": "http://localhost:5173",
            },
            json=request_payload(),
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "workspace_id": 1,
        "connection_id": 30,
        "provider": "espn",
        "provider_league_id": "529250970",
        "provider_season_id": "2026",
        "credential_version": 1,
        "credential_schema_version": 1,
        "validated_at": VALIDATED_AT.isoformat().replace(
            "+00:00",
            "Z",
        ),
    }

    serialized = response.text
    assert "replacement-cookie" not in serialized
    assert "replacement-swid" not in serialized
    assert "encrypted_credentials" not in serialized


def test_spectator_cannot_replace_provider_credentials() -> None:
    apply_overrides(
        current_user=override_spectator,
    )

    try:
        response = client.patch(
            "/api/admin/provider-credentials",
            headers={
                "Origin": "http://localhost:5173",
            },
            json=request_payload(),
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_validation_failure_is_safe() -> None:
    def reject(**_):
        raise ProviderCredentialReplacementValidationError(
            "The fantasy provider credentials were rejected."
        )

    apply_overrides(reject)

    try:
        response = client.patch(
            "/api/admin/provider-credentials",
            headers={
                "Origin": "http://localhost:5173",
            },
            json=request_payload(),
        )
    finally:
        clear_overrides()

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
    assert response.json() == {"detail": "The fantasy provider credentials were rejected."}
    assert "replacement-cookie" not in response.text


def test_unavailable_context_returns_conflict() -> None:
    def unavailable(**_):
        raise ProviderCredentialReplacementUnavailableError(
            "The workspace provider claim is unavailable."
        )

    apply_overrides(unavailable)

    try:
        response = client.patch(
            "/api/admin/provider-credentials",
            headers={
                "Origin": "http://localhost:5173",
            },
            json=request_payload(),
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": "The workspace provider claim is unavailable."}


def test_change_during_validation_returns_conflict_without_credentials() -> None:
    def changed(**_):
        raise ProviderCredentialReplacementUnavailableError(CHANGED_DURING_VALIDATION_MESSAGE)

    apply_overrides(changed)

    try:
        response = client.patch(
            "/api/admin/provider-credentials",
            headers={
                "Origin": "http://localhost:5173",
            },
            json=request_payload(),
        )
    finally:
        clear_overrides()

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": CHANGED_DURING_VALIDATION_MESSAGE}
    assert "replacement-cookie" not in response.text
    assert "replacement-swid" not in response.text


def test_missing_encryption_configuration_is_safe() -> None:
    def missing_key(**_):
        raise ProviderCredentialConfigurationError("secret deployment detail")

    apply_overrides(missing_key)

    try:
        response = client.patch(
            "/api/admin/provider-credentials",
            headers={
                "Origin": "http://localhost:5173",
            },
            json=request_payload(),
        )
    finally:
        clear_overrides()

    assert response.status_code == (status.HTTP_503_SERVICE_UNAVAILABLE)
    assert response.json() == {"detail": "Provider credential encryption is not configured."}
    assert "secret deployment detail" not in response.text


def test_empty_credentials_are_rejected() -> None:
    apply_overrides()

    try:
        response = client.patch(
            "/api/admin/provider-credentials",
            headers={
                "Origin": "http://localhost:5173",
            },
            json={
                "espn_s2": " ",
                "swid": " ",
            },
        )
    finally:
        clear_overrides()

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
    assert response.json() == {"detail": "Both ESPN_S2 and SWID credentials are required."}


@pytest.mark.parametrize(
    "payload",
    [
        {
            "espn_s2": "",
            "swid": "{replacement-swid}",
        },
        {
            "espn_s2": "replacement-cookie",
            "swid": "",
        },
    ],
)
def test_partial_credentials_are_rejected(
    payload: dict[str, str],
) -> None:
    invoked = False

    def replacer(**_):
        nonlocal invoked
        invoked = True
        raise AssertionError("The credential replacer must not receive partial credentials.")

    apply_overrides(replacer)

    try:
        response = client.patch(
            "/api/admin/provider-credentials",
            headers={
                "Origin": "http://localhost:5173",
            },
            json=payload,
        )
    finally:
        clear_overrides()

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
    assert response.json() == {"detail": "Both ESPN_S2 and SWID credentials are required."}
    assert invoked is False
