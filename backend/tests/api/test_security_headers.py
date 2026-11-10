import os
from unittest.mock import patch

from fastapi import Response
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import app
from app.security_headers import (
    CONTENT_SECURITY_POLICY,
    HSTS_VALUE,
    PERMISSIONS_POLICY,
    apply_security_headers,
    requires_no_store,
)

client = TestClient(app)


def assert_standard_security_headers(
    response,
) -> None:
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == PERMISSIONS_POLICY


def test_health_response_has_security_headers() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert_standard_security_headers(response)
    assert "Strict-Transport-Security" not in (response.headers)


def test_protected_response_has_security_headers() -> None:
    response = client.get("/api/leagues/current")

    assert response.status_code in {
        200,
        401,
    }
    assert_standard_security_headers(response)


def test_validation_response_has_security_headers() -> None:
    response = client.get(
        "/api/analytics/schedule-swap",
    )

    assert response.status_code in {
        401,
        422,
    }
    assert_standard_security_headers(response)


def test_authentication_response_disables_caching() -> None:
    response = client.get("/api/auth/csrf")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_setup_response_disables_caching() -> None:
    response = client.get("/api/setup/status")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_admin_response_disables_caching() -> None:
    response = client.get("/api/admin/sync-status")

    assert response.status_code in {
        200,
        401,
        403,
        404,
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert_standard_security_headers(response)


def test_regular_analytics_response_is_not_forced_no_store() -> None:
    response = client.get("/api/analytics/all-play")

    assert response.status_code in {
        200,
        401,
    }
    assert "Cache-Control" not in response.headers
    assert_standard_security_headers(response)


def test_sensitive_path_classification() -> None:
    assert requires_no_store("/api/auth/login")
    assert requires_no_store("/api/setup/status")
    assert requires_no_store("/api/admin/spectator")
    assert not requires_no_store("/api/leagues/current")
    assert not requires_no_store("/api/analytics/all-play")
    assert not requires_no_store("/health")


def test_production_adds_hsts() -> None:
    environment = {
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "APP_SECRET_KEY": ("production-test-secret-with-more-than-32-characters"),
        "FRONTEND_ORIGIN": ("https://fantasy.example.com"),
        "TRUSTED_ORIGINS": ("https://fantasy.example.com"),
        "FORWARDED_ALLOW_IPS": "172.30.0.10",
        "SESSION_COOKIE_NAME": ("__Host-fantasy_session"),
        "SESSION_COOKIE_SECURE": "true",
        "SESSION_COOKIE_SAMESITE": "lax",
        "DATABASE_URL": ("postgresql+psycopg://user:password@database/db"),
    }

    with patch.dict(
        os.environ,
        environment,
        clear=True,
    ):
        settings = Settings(_env_file=None)

    response = Response()

    apply_security_headers(
        response,
        request_path="/health",
        settings=settings,
    )

    assert response.headers["Strict-Transport-Security"] == HSTS_VALUE


def test_development_does_not_add_hsts() -> None:
    response = Response()

    from app.core.settings import get_settings

    apply_security_headers(
        response,
        request_path="/health",
        settings=get_settings(),
    )

    assert "Strict-Transport-Security" not in (response.headers)
