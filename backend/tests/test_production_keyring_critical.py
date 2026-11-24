"""Critical production fail-closed keyring tests."""

import json

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings

SECRET_MARKER = "do-not-leak-this-key-material"


def fernet_keyring(*versions: int) -> SecretStr:
    return SecretStr(json.dumps({str(v): Fernet.generate_key().decode() for v in versions}))


def production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "app_debug": False,
        "app_secret_key": SecretStr("s" * 48),
        "frontend_origin": "https://staging.example.test",
        "trusted_origins": "https://staging.example.test",
        "forwarded_allow_ips": "172.31.100.10",
        "session_cookie_name": "__Host-fantasy_session",
        "session_cookie_secure": True,
        "database_url": "postgresql+psycopg://user:password@database:5432/fantasy",
        "provider_credential_keys": fernet_keyring(1),
        "transactional_email_keys": fernet_keyring(1),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_valid_production_settings_start() -> None:
    assert production_settings().is_production


@pytest.mark.parametrize("field", ["provider_credential_keys", "transactional_email_keys"])
@pytest.mark.parametrize("value", [None, SecretStr(""), SecretStr("   "), SecretStr("{}")])
def test_production_rejects_absent_or_empty_keyring(field: str, value) -> None:
    with pytest.raises(ValidationError) as error:
        production_settings(**{field: value})

    assert field.upper() in str(error.value)


@pytest.mark.parametrize("field", ["provider_credential_keys", "transactional_email_keys"])
def test_production_rejects_malformed_keyring_without_leaking_it(field: str) -> None:
    with pytest.raises(ValidationError) as error:
        production_settings(**{field: SecretStr(f"{{not-json {SECRET_MARKER}")})

    assert SECRET_MARKER not in str(error.value)


@pytest.mark.parametrize(
    ("keys_field", "version_field"),
    [
        ("provider_credential_keys", "provider_credential_current_key_version"),
        ("transactional_email_keys", "transactional_email_current_key_version"),
    ],
)
def test_production_rejects_missing_current_version(keys_field: str, version_field: str) -> None:
    with pytest.raises(ValidationError) as error:
        production_settings(**{keys_field: fernet_keyring(1), version_field: 2})

    assert "key" in str(error.value).lower()


def test_error_messages_never_contain_key_material() -> None:
    keyring = fernet_keyring(1)

    with pytest.raises(ValidationError) as error:
        production_settings(
            provider_credential_keys=keyring, provider_credential_current_key_version=2
        )

    assert keyring.get_secret_value() not in str(error.value)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_may_omit_keyrings(environment: str) -> None:
    settings = Settings(
        _env_file=None,
        app_env=environment,
        app_debug=True,
        app_secret_key=SecretStr("x" * 48),
        database_url="postgresql+psycopg://user:password@database:5432/fantasy",
    )

    assert not settings.provider_credential_keyring().configured
    assert not settings.transactional_email_keyring().configured


def test_production_does_not_require_resend_key_when_email_disabled() -> None:
    settings = production_settings(
        transactional_email_enabled=False, transactional_email_api_key=None
    )

    assert settings.transactional_email_api_key is None
