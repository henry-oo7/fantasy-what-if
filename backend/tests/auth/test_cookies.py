from fastapi import Response
from pydantic import SecretStr

from app.auth.cookies import (
    clear_authentication_cookies,
    csrf_cookie_name,
    set_authentication_cookies,
)
from app.core.settings import Settings


def create_settings(
    *,
    production: bool,
) -> Settings:
    origin = "https://fantasy.example.com" if production else "http://localhost:5173"

    return Settings(
        app_env=("production" if production else "test"),
        app_debug=not production,
        app_secret_key=SecretStr("x" * 48),
        frontend_origin=origin,
        trusted_origins=origin,
        session_cookie_name=("__Host-fantasy_session" if production else "fantasy_session"),
        session_cookie_secure=production,
        session_cookie_samesite="lax",
        espn_s2=SecretStr("test-espn-s2"),
        espn_swid=SecretStr("{test-swid}"),
        database_url=("postgresql+psycopg://user:password@database:5432/fantasy"),
    )


def test_development_cookie_names() -> None:
    settings = create_settings(production=False)

    assert settings.session_cookie_name == "fantasy_session"
    assert csrf_cookie_name(settings) == "fantasy_session_csrf"


def test_production_cookie_names() -> None:
    settings = create_settings(production=True)

    assert settings.session_cookie_name == "__Host-fantasy_session"
    assert csrf_cookie_name(settings) == "__Host-fantasy_csrf"


def test_authentication_cookies_have_expected_flags() -> None:
    settings = create_settings(production=False)
    response = Response()

    set_authentication_cookies(
        response,
        settings=settings,
        session_token="session-token",
        csrf_token="csrf-token",
    )

    headers = response.headers.getlist("set-cookie")

    assert len(headers) == 2

    session_header = next(header for header in headers if header.startswith("fantasy_session="))
    csrf_header = next(header for header in headers if header.startswith("fantasy_session_csrf="))

    assert "HttpOnly" in session_header
    assert "SameSite=lax" in session_header
    assert "HttpOnly" not in csrf_header
    assert "SameSite=lax" in csrf_header


def test_production_cookies_are_secure() -> None:
    settings = create_settings(production=True)
    response = Response()

    set_authentication_cookies(
        response,
        settings=settings,
        session_token="session-token",
        csrf_token="csrf-token",
    )

    headers = response.headers.getlist("set-cookie")

    assert all("Secure" in header for header in headers)


def test_logout_clears_both_cookies() -> None:
    settings = create_settings(production=False)
    response = Response()

    clear_authentication_cookies(
        response,
        settings=settings,
    )

    headers = response.headers.getlist("set-cookie")

    assert len(headers) == 2
    assert all("Max-Age=0" in header for header in headers)
