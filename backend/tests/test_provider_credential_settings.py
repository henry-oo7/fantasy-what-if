import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings


def validate_key(
    value: SecretStr | None,
) -> SecretStr | None:
    validator = Settings.__pydantic_decorators__.field_validators[
        "validate_provider_credential_key"
    ].func

    return validator(value)


def test_provider_credential_key_is_optional() -> None:
    assert validate_key(None) is None


def test_blank_provider_credential_key_becomes_none() -> None:
    assert validate_key(SecretStr("   ")) is None


def test_valid_provider_credential_key_is_accepted() -> None:
    key = Fernet.generate_key().decode("ascii")

    result = validate_key(SecretStr(f"  {key}  "))

    assert result is not None
    assert result.get_secret_value() == key


@pytest.mark.parametrize(
    "value",
    [
        "not-a-fernet-key",
        "short",
        "x" * 44,
        "not-ascii-\N{SNOWMAN}",
    ],
)
def test_invalid_provider_credential_key_is_rejected(
    value: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="valid Fernet key",
    ):
        validate_key(SecretStr(value))


def test_setting_field_defaults_to_none() -> None:
    field = Settings.model_fields["provider_credential_key"]

    assert field.default is None


def test_invalid_environment_value_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PROVIDER_CREDENTIAL_KEY",
        "not-a-fernet-key",
    )

    with pytest.raises(ValidationError):
        Settings()
