from pydantic import SecretStr

from app.auth.csrf import (
    PRE_AUTH_CSRF_LIFETIME_SECONDS,
    generate_pre_auth_csrf_token,
    validate_double_submit_csrf,
    validate_pre_auth_csrf_token,
)
from app.core.settings import Settings


def create_settings() -> Settings:
    return Settings(
        app_env="test",
        app_debug=True,
        app_secret_key=SecretStr("x" * 48),
        frontend_origin="http://localhost:5173",
        trusted_origins="http://localhost:5173",
        session_cookie_name="fantasy_session",
        session_cookie_secure=False,
        espn_s2=SecretStr("test-espn-s2"),
        espn_swid=SecretStr("{test-swid}"),
        database_url=("postgresql+psycopg://user:password@database:5432/fantasy"),
    )


def test_signed_csrf_token_is_valid() -> None:
    settings = create_settings()
    issued_at = 1_000_000

    token = generate_pre_auth_csrf_token(
        settings,
        timestamp=issued_at,
    )

    assert validate_pre_auth_csrf_token(
        token,
        settings=settings,
        now=issued_at + 60,
    )


def test_tampered_csrf_token_is_rejected() -> None:
    settings = create_settings()
    issued_at = 1_000_000

    token = generate_pre_auth_csrf_token(
        settings,
        timestamp=issued_at,
    )

    assert not validate_pre_auth_csrf_token(
        token + "tampered",
        settings=settings,
        now=issued_at + 60,
    )


def test_expired_csrf_token_is_rejected() -> None:
    settings = create_settings()
    issued_at = 1_000_000

    token = generate_pre_auth_csrf_token(
        settings,
        timestamp=issued_at,
    )

    assert not validate_pre_auth_csrf_token(
        token,
        settings=settings,
        now=(issued_at + PRE_AUTH_CSRF_LIFETIME_SECONDS + 1),
    )


def test_future_csrf_token_is_rejected() -> None:
    settings = create_settings()

    token = generate_pre_auth_csrf_token(
        settings,
        timestamp=2_000_000,
    )

    assert not validate_pre_auth_csrf_token(
        token,
        settings=settings,
        now=1_000_000,
    )


def test_double_submit_requires_cookie_header_match() -> None:
    settings = create_settings()
    issued_at = 1_000_000

    token = generate_pre_auth_csrf_token(
        settings,
        timestamp=issued_at,
    )

    assert validate_double_submit_csrf(
        cookie_token=token,
        header_token=token,
        settings=settings,
        now=issued_at + 60,
    )

    assert not validate_double_submit_csrf(
        cookie_token=token,
        header_token="different-token",
        settings=settings,
        now=issued_at + 60,
    )

    assert not validate_double_submit_csrf(
        cookie_token=None,
        header_token=token,
        settings=settings,
        now=issued_at + 60,
    )
