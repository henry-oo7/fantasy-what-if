import json

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings

REQUIRED_SETTINGS = {
    "app_secret_key": SecretStr("x" * 48),
    "database_url": ("postgresql+psycopg://user:password@localhost/fantasy"),
}


def test_versioned_provider_credential_keyring_loads() -> None:
    first_key = Fernet.generate_key().decode("ascii")
    second_key = Fernet.generate_key().decode("ascii")

    settings = Settings(
        _env_file=None,
        **REQUIRED_SETTINGS,
        provider_credential_keys=SecretStr(
            json.dumps(
                {
                    "1": first_key,
                    "2": second_key,
                }
            )
        ),
        provider_credential_current_key_version=2,
    )

    keyring = settings.provider_credential_keyring()

    assert keyring.current_version == 2
    assert keyring.current_key == second_key
    assert keyring.key_for_version(1) == first_key
    assert keyring.key_for_version(2) == second_key


def test_unconfigured_keyring_remains_explicit() -> None:
    settings = Settings(
        _env_file=None,
        **REQUIRED_SETTINGS,
    )

    keyring = settings.provider_credential_keyring()

    assert keyring.configured is False


def test_current_provider_credential_key_version_must_exist() -> None:
    with pytest.raises(
        ValidationError,
        match="current.*not configured",
    ):
        Settings(
            _env_file=None,
            **REQUIRED_SETTINGS,
            provider_credential_keys=SecretStr(
                json.dumps({"1": (Fernet.generate_key().decode("ascii"))})
            ),
            provider_credential_current_key_version=2,
        )


def test_invalid_versioned_environment_keyring_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PROVIDER_CREDENTIAL_KEYS",
        "not-json",
    )
    monkeypatch.setenv(
        "PROVIDER_CREDENTIAL_CURRENT_KEY_VERSION",
        "1",
    )

    with pytest.raises(
        ValidationError,
        match="valid JSON object",
    ):
        Settings(
            _env_file=None,
            **REQUIRED_SETTINGS,
        )


def test_keyring_settings_have_saas_defaults() -> None:
    keys_field = Settings.model_fields["provider_credential_keys"]
    current_field = Settings.model_fields["provider_credential_current_key_version"]

    assert keys_field.default is None
    assert current_field.default == 1


def test_settings_repr_does_not_expose_keyring_material() -> None:
    key = Fernet.generate_key().decode("ascii")

    settings = Settings(
        _env_file=None,
        **REQUIRED_SETTINGS,
        provider_credential_keys=SecretStr(
            json.dumps(
                {
                    "1": key,
                }
            )
        ),
        provider_credential_current_key_version=1,
    )

    assert key not in repr(settings)
