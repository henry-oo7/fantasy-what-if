"""API documentation routes must be absent in production only."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable, Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.settings import Settings, get_settings

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
VARIANT_MODULE = "app_main_documentation_variant"


def _keyring() -> str:
    return json.dumps({"1": Fernet.generate_key().decode()})


def _apply_environment(monkeypatch: pytest.MonkeyPatch, app_env: str) -> None:
    production = app_env == "production"
    # Never read a developer's real .env file.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    values = {
        "APP_ENV": app_env,
        "APP_DEBUG": "false" if production else "true",
        "APP_SECRET_KEY": "s" * 48,
        "FRONTEND_ORIGIN": "https://fantasy.test" if production else "http://localhost:5173",
        "TRUSTED_ORIGINS": "https://fantasy.test" if production else "http://localhost:5173",
        "FORWARDED_ALLOW_IPS": "172.31.100.10",
        "SESSION_COOKIE_NAME": "__Host-fantasy_session" if production else "fantasy_session",
        "SESSION_COOKIE_SECURE": "true" if production else "false",
        "SESSION_COOKIE_SAMESITE": "lax",
        "DATABASE_URL": "postgresql+psycopg://synthetic:synthetic@localhost:5432/synthetic",
        "PROVIDER_CREDENTIAL_KEYS": _keyring(),
        "PROVIDER_CREDENTIAL_CURRENT_KEY_VERSION": "1",
        "TRANSACTIONAL_EMAIL_KEYS": _keyring(),
        "TRANSACTIONAL_EMAIL_CURRENT_KEY_VERSION": "1",
        "TRANSACTIONAL_EMAIL_ENABLED": "false",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def build_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], FastAPI]]:
    """Build a separate app.main instance for an environment.

    The variant is loaded under a private module name so the shared app.main
    module and its app object stay untouched. The settings cache is cleared
    before and after every build.
    """

    spec = importlib.util.find_spec("app.main")
    assert spec is not None and spec.origin is not None
    origin = spec.origin

    def _build(app_env: str) -> FastAPI:
        _apply_environment(monkeypatch, app_env)
        get_settings.cache_clear()
        variant_spec = importlib.util.spec_from_file_location(VARIANT_MODULE, origin)
        assert variant_spec is not None and variant_spec.loader is not None
        module = importlib.util.module_from_spec(variant_spec)
        sys.modules[VARIANT_MODULE] = module
        variant_spec.loader.exec_module(module)
        assert module.settings.app_env == app_env
        return module.app

    try:
        yield _build
    finally:
        sys.modules.pop(VARIANT_MODULE, None)
        get_settings.cache_clear()


def test_production_hides_documentation_routes(build_app) -> None:
    app = build_app("production")
    assert (app.docs_url, app.redoc_url, app.openapi_url) == (None, None, None)

    client = TestClient(app)
    for path in DOC_PATHS:
        assert client.get(path).status_code == 404, path


def test_production_keeps_health_and_protected_routes(build_app) -> None:
    client = TestClient(build_app("production"))

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    protected = client.get("/api/leagues/current")
    assert protected.status_code in {200, 401}


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_non_production_keeps_documentation_routes(build_app, app_env: str) -> None:
    app = build_app(app_env)
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"

    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "Fantasy What-If API"


def test_variant_build_does_not_change_shared_application(build_app) -> None:
    from app.main import app as shared_app

    before = (shared_app.docs_url, shared_app.openapi_url)
    build_app("production")
    assert (shared_app.docs_url, shared_app.openapi_url) == before
    assert "app.main" in sys.modules
