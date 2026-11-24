"""Provider-neutral transactional-email settings (ADR 0009)."""

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings

SYNTHETIC_KEY = "re_synthetic_settings_key_000"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def create_settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "app_debug": True,
        "app_secret_key": SecretStr("x" * 48),
        "frontend_origin": "http://localhost:5173",
        "trusted_origins": "http://localhost:5173",
        "session_cookie_name": "fantasy_session",
        "session_cookie_secure": False,
        "database_url": "postgresql+psycopg://user:password@database:5432/fantasy",
        "transactional_email_enabled": False,
        "transactional_email_provider": "resend",
        "transactional_email_from_address": None,
        "transactional_email_from_name": "Fantasy What-If",
        "transactional_email_public_base_url": None,
        "transactional_email_api_key": None,
        "transactional_email_connect_timeout_seconds": 3,
        "transactional_email_read_timeout_seconds": 7,
    }
    values.update(overrides)

    return Settings(_env_file=None, **values)


def enabled(**overrides) -> Settings:
    values = {
        "transactional_email_enabled": True,
        "transactional_email_api_key": SecretStr(SYNTHETIC_KEY),
        "transactional_email_from_address": "notifications@synthetic.invalid",
        "transactional_email_public_base_url": "https://fantasy.synthetic.invalid",
    }
    values.update(overrides)
    return create_settings(**values)


def production(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "app_debug": False,
        "frontend_origin": "https://fantasy.synthetic.invalid",
        "trusted_origins": "https://fantasy.synthetic.invalid",
        "session_cookie_name": "__Host-fantasy_session",
        "session_cookie_secure": True,
        "forwarded_allow_ips": "127.0.0.1",
        "provider_credential_keys": SecretStr(json.dumps({"1": Fernet.generate_key().decode()})),
        "transactional_email_keys": SecretStr(json.dumps({"1": Fernet.generate_key().decode()})),
    }
    values.update(overrides)
    return enabled(**values)


def test_defaults_are_disabled_resend_with_bounded_timeouts() -> None:
    settings = create_settings()

    assert settings.transactional_email_enabled is False
    assert settings.transactional_email_provider == "resend"
    assert settings.transactional_email_from_name == "Fantasy What-If"
    assert settings.transactional_email_connect_timeout_seconds == 3
    assert settings.transactional_email_read_timeout_seconds == 7
    assert settings.transactional_email_api_key is None


def test_disabled_delivery_requires_no_api_key_sender_or_base_url() -> None:
    assert create_settings().transactional_email_enabled is False


@pytest.mark.parametrize(
    ("missing", "setting"),
    [
        ({"transactional_email_api_key": None}, "TRANSACTIONAL_EMAIL_API_KEY"),
        ({"transactional_email_api_key": SecretStr("   ")}, "TRANSACTIONAL_EMAIL_API_KEY"),
        ({"transactional_email_from_address": None}, "TRANSACTIONAL_EMAIL_FROM_ADDRESS"),
        ({"transactional_email_public_base_url": None}, "TRANSACTIONAL_EMAIL_PUBLIC_BASE_URL"),
    ],
)
def test_enabled_delivery_requires_key_sender_and_base_url(missing: dict, setting: str) -> None:
    with pytest.raises(ValidationError, match=setting):
        enabled(**missing)


def test_enabled_delivery_accepts_complete_configuration() -> None:
    settings = enabled()

    assert settings.transactional_email_api_key is not None
    assert settings.transactional_email_api_key.get_secret_value() == SYNTHETIC_KEY
    assert SYNTHETIC_KEY not in repr(settings)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Fantasy.Synthetic.Invalid/", "https://fantasy.synthetic.invalid"),
        ("https://fantasy.synthetic.invalid", "https://fantasy.synthetic.invalid"),
        ("https://fantasy.synthetic.invalid:8443/", "https://fantasy.synthetic.invalid:8443"),
        ("http://localhost:5173/", "http://localhost:5173"),
    ],
)
def test_public_base_url_is_normalized(value: str, expected: str) -> None:
    assert enabled(
        transactional_email_public_base_url=value
    ).transactional_email_public_base_url == (expected)


@pytest.mark.parametrize(
    "value",
    [
        "fantasy.synthetic.invalid",
        "/verify-email",
        "ftp://fantasy.synthetic.invalid",
        "https://user:pass@fantasy.synthetic.invalid",
        "https://user@fantasy.synthetic.invalid",
        "https://fantasy.synthetic.invalid/?next=1",
        "https://fantasy.synthetic.invalid/#fragment",
        "https://fantasy.synthetic.invalid/app",
        "https://",
    ],
)
def test_public_base_url_rejects_non_origins_without_echoing(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        enabled(transactional_email_public_base_url=value)

    assert "TRANSACTIONAL_EMAIL_PUBLIC_BASE_URL" in str(caught.value)
    assert "pass@" not in str(caught.value).split("input_value")[0]


def test_public_base_url_requires_https_in_production() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        production(transactional_email_public_base_url="http://fantasy.synthetic.invalid")

    settings = production()
    assert settings.transactional_email_public_base_url == "https://fantasy.synthetic.invalid"


@pytest.mark.parametrize(
    "value",
    [
        "not-an-address",
        "two@@synthetic.invalid",
        "Name <notifications@synthetic.invalid>",
        "notifications@synthetic",
        "notifications@synthetic.invalid\r\nBcc: x@synthetic.invalid",
        ".dot@synthetic.invalid",
    ],
)
def test_sender_address_must_be_valid(value: str) -> None:
    with pytest.raises(ValidationError, match="TRANSACTIONAL_EMAIL_FROM_ADDRESS"):
        enabled(transactional_email_from_address=value)


def test_sender_address_domain_is_lowercased() -> None:
    settings = enabled(transactional_email_from_address="Notify@Synthetic.Invalid")

    assert settings.transactional_email_from_address == "Notify@synthetic.invalid"


@pytest.mark.parametrize("value", ["", "Bad <Name>", 'Quote"d', "Line\nBreak", "a" * 101])
def test_sender_name_rejects_header_unsafe_values(value: str) -> None:
    with pytest.raises(ValidationError, match="TRANSACTIONAL_EMAIL_FROM_NAME"):
        create_settings(transactional_email_from_name=value)


def test_unsupported_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        create_settings(transactional_email_provider="smtp")


@pytest.mark.parametrize(
    "field",
    [
        "transactional_email_connect_timeout_seconds",
        "transactional_email_read_timeout_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, 120])
def test_timeouts_are_positive_and_bounded(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        create_settings(**{field: value})


def test_delivery_settings_are_independent_of_recipient_keyring() -> None:
    settings = enabled(transactional_email_keys=None)

    assert settings.transactional_email_keyring().configured is False
    assert settings.transactional_email_api_key is not None


@pytest.mark.parametrize("template", [".env.example", ".env.production.example"])
def test_environment_templates_contain_placeholders_only(template: str) -> None:
    lines = (REPOSITORY_ROOT / template).read_text().splitlines()
    email_lines = [line for line in lines if "TRANSACTIONAL_EMAIL_" in line and "=" in line]
    names = {line.lstrip("# ").split("=", 1)[0] for line in email_lines}

    assert {
        "TRANSACTIONAL_EMAIL_ENABLED",
        "TRANSACTIONAL_EMAIL_PROVIDER",
        "TRANSACTIONAL_EMAIL_FROM_ADDRESS",
        "TRANSACTIONAL_EMAIL_FROM_NAME",
        "TRANSACTIONAL_EMAIL_PUBLIC_BASE_URL",
        "TRANSACTIONAL_EMAIL_API_KEY",
        "TRANSACTIONAL_EMAIL_CONNECT_TIMEOUT_SECONDS",
        "TRANSACTIONAL_EMAIL_READ_TIMEOUT_SECONDS",
        "TRANSACTIONAL_EMAIL_KEYS",
        "TRANSACTIONAL_EMAIL_CURRENT_KEY_VERSION",
    } <= names

    api_key_line = next(line for line in email_lines if "TRANSACTIONAL_EMAIL_API_KEY=" in line)
    assert api_key_line.startswith("#")
    assert "replace-with" in api_key_line
    assert "re_" not in api_key_line.split("=", 1)[1]
