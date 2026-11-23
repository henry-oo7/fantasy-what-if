"""Retired single-installation setup and authentication paths stay retired.

The hosted SaaS deployment is new: SaaS onboarding is the only setup path,
administrators sign in by email, spectators by workspace slug and shared
password, and provider access comes only from persisted workspace
connections encrypted with the versioned keyring.
"""

from collections.abc import Iterator

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.auth_dependencies import get_current_user
from app.core.settings import Settings
from app.db.auth_models import User
from app.db.models import Season
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import Installation, ProviderConnection
from app.db.session import SessionFactory
from app.main import app
from app.services.provider_adapter_resolver import (
    ProviderAdapterResolutionError,
    resolve_connection_credentials,
)

TRUSTED_ORIGIN = "http://localhost:5173"

RETIRED_REQUESTS = (
    ("GET", "/api/setup/status"),
    ("GET", "/api/setup/providers"),
    ("POST", "/api/setup/bootstrap-admin"),
    ("POST", "/api/setup/provider-connection"),
    ("POST", "/api/setup/finalize"),
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/login"),
)

RETIRED_ENVIRONMENT = {
    "PROVIDER_CREDENTIAL_KEY": "retired-single-value-key",
    "ESPN_S2": "retired-environment-cookie",
    "ESPN_SWID": "{retired-environment-swid}",
    "ESPN_LEAGUE_ID": "123456",
    "ESPN_SEASON": "2026",
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    inherited_override = app.dependency_overrides.pop(get_current_user, None)

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        if inherited_override is not None:
            app.dependency_overrides[get_current_user] = inherited_override


def _api_paths() -> set[str]:
    # No application route opts out of the schema, so the OpenAPI path map
    # is the complete registered API surface.
    return set(app.openapi()["paths"])


@pytest.mark.parametrize(("method", "path"), RETIRED_REQUESTS)
def test_retired_endpoints_use_ordinary_not_found_routing(
    client: TestClient, method: str, path: str
) -> None:
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]

    response = client.request(
        method,
        path,
        headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf},
        json={"username": "legacy-admin", "password": "correct horse battery staple"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Not Found"}


def test_no_hidden_setup_or_username_login_alias_is_registered() -> None:
    paths = _api_paths()

    assert not any(path.startswith("/api/setup") for path in paths)
    assert "/api/auth/login" not in paths
    assert not any("bootstrap" in path for path in paths)
    # The modern sign-in endpoints remain.
    assert {"/api/auth/admin/login", "/api/auth/spectator/login"} <= paths


def test_public_account_routes_remain_registered() -> None:
    assert {
        "/api/auth/csrf",
        "/api/auth/me",
        "/api/auth/logout",
        "/api/auth/password-reset/request",
        "/api/auth/password-reset/complete",
        "/api/registration",
        "/api/registration/verify-email",
        "/api/registration/resend-verification",
        "/api/onboarding/activation",
        "/api/onboarding/activate",
    } <= _api_paths()


def test_startup_never_bootstraps_an_installation_or_account() -> None:
    with SessionFactory() as session:
        before = (
            session.scalar(select(func.count()).select_from(Installation)),
            session.scalar(select(func.count()).select_from(User)),
            session.scalar(select(func.count()).select_from(ProviderConnection)),
        )

    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == status.HTTP_200_OK

    with SessionFactory() as session:
        after = (
            session.scalar(select(func.count()).select_from(Installation)),
            session.scalar(select(func.count()).select_from(User)),
            session.scalar(select(func.count()).select_from(ProviderConnection)),
        )

    assert after == before


def test_settings_expose_only_the_versioned_provider_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in RETIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)

    settings = Settings()
    fields = set(Settings.model_fields)

    assert "provider_credential_keys" in fields
    assert "provider_credential_current_key_version" in fields
    assert "provider_credential_key" not in fields
    assert not any(field.startswith("espn") for field in fields)
    assert not hasattr(settings, "normalize_swid")
    assert not hasattr(settings, "provider_credential_key")


def test_environment_credentials_cannot_construct_provider_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in RETIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)

    connection = ProviderConnection(
        id=1,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        display_name="ESPN",
        encrypted_credentials=None,
        credential_version=None,
        is_active=True,
    )

    with pytest.raises(ProviderAdapterResolutionError, match="does not have stored credentials"):
        resolve_connection_credentials(
            connection=connection,
            season=Season(id=1),
            settings=Settings(),
        )


def _schema_property_names(schema: object) -> Iterator[str]:
    if isinstance(schema, dict):
        properties = schema.get("properties")

        if isinstance(properties, dict):
            yield from properties

        for value in schema.values():
            yield from _schema_property_names(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _schema_property_names(value)


def test_no_modern_api_accepts_or_returns_internal_username() -> None:
    openapi = app.openapi()

    assert "username" not in set(_schema_property_names(openapi))
