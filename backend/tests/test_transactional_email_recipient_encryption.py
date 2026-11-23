import json
import logging

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings
from app.notifications.recipient_encryption import (
    TransactionalEmailKeyring,
    TransactionalEmailKeyringConfigurationError,
    TransactionalEmailRecipientCipher,
    TransactionalEmailRecipientError,
    build_transactional_email_keyring,
)
from app.providers import credential_keyring

REQUIRED = {
    "app_secret_key": SecretStr("x" * 48),
    "database_url": "postgresql+psycopg://user:password@localhost/fantasy",
}
RECIPIENT = "owner@synthetic.invalid"


def fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


def keyring(*versions: int, current: int | None = None) -> TransactionalEmailKeyring:
    return TransactionalEmailKeyring(
        keys={version: fernet_key() for version in versions},
        current_version=current or versions[-1],
    )


def test_keyring_parses_versioned_keys_from_settings() -> None:
    first, second = fernet_key(), fernet_key()
    settings = Settings(
        _env_file=None,
        **REQUIRED,
        transactional_email_keys=SecretStr(json.dumps({"1": first, "2": second})),
        transactional_email_current_key_version=2,
    )

    parsed = settings.transactional_email_keyring()

    assert parsed.current_version == 2
    assert parsed.key_for_version(1) == first
    assert parsed.key_for_version(2) == second


def test_unconfigured_keyring_is_explicit_and_cipher_refuses_it() -> None:
    parsed = Settings(_env_file=None, **REQUIRED).transactional_email_keyring()

    assert parsed.configured is False

    with pytest.raises(TransactionalEmailKeyringConfigurationError):
        TransactionalEmailRecipientCipher(parsed)


def test_current_version_must_exist() -> None:
    with pytest.raises(ValidationError, match="current.*not configured"):
        Settings(
            _env_file=None,
            **REQUIRED,
            transactional_email_keys=SecretStr(json.dumps({"1": fernet_key()})),
            transactional_email_current_key_version=2,
        )


@pytest.mark.parametrize(
    "serialized",
    ["not-json", "[]", "{}", '{"0": "x"}', '{"01": "x"}', '{"-1": "x"}', '{"a": "x"}', '{"1": 5}'],
)
def test_invalid_keyrings_are_rejected(serialized: str) -> None:
    with pytest.raises(TransactionalEmailKeyringConfigurationError):
        build_transactional_email_keyring(serialized_keys=SecretStr(serialized), current_version=1)


def test_non_fernet_key_error_does_not_reveal_key_material() -> None:
    secret = "definitely-not-a-fernet-key"

    with pytest.raises(TransactionalEmailKeyringConfigurationError) as caught:
        build_transactional_email_keyring(
            serialized_keys=SecretStr(json.dumps({"1": secret})),
            current_version=1,
        )

    assert secret not in str(caught.value)


def test_round_trip_ciphertext_differs_and_records_current_version() -> None:
    cipher = TransactionalEmailRecipientCipher(keyring(1, 2, current=2))

    encrypted = cipher.encrypt(recipient=RECIPIENT, delivery_id=7)

    assert encrypted.key_version == 2
    assert RECIPIENT not in encrypted.ciphertext
    assert RECIPIENT not in repr(encrypted)
    assert (
        cipher.decrypt(
            ciphertext=encrypted.ciphertext,
            key_version=encrypted.key_version,
            delivery_id=7,
        )
        == RECIPIENT
    )


def test_rotation_decrypts_older_version() -> None:
    old_key = fernet_key()
    old_cipher = TransactionalEmailRecipientCipher(
        TransactionalEmailKeyring(keys={1: old_key}, current_version=1)
    )
    encrypted = old_cipher.encrypt(recipient=RECIPIENT, delivery_id=3)

    rotated = TransactionalEmailRecipientCipher(
        TransactionalEmailKeyring(keys={1: old_key, 2: fernet_key()}, current_version=2)
    )

    assert rotated.encrypt(recipient=RECIPIENT, delivery_id=3).key_version == 2
    assert (
        rotated.decrypt(ciphertext=encrypted.ciphertext, key_version=1, delivery_id=3) == RECIPIENT
    )


def test_ciphertext_is_bound_to_delivery_id_and_key_version() -> None:
    cipher = TransactionalEmailRecipientCipher(keyring(1, 2, current=1))
    encrypted = cipher.encrypt(recipient=RECIPIENT, delivery_id=5)

    for kwargs in (
        {"key_version": 1, "delivery_id": 6},
        {"key_version": 2, "delivery_id": 5},
        {"key_version": 9, "delivery_id": 5},
    ):
        with pytest.raises(TransactionalEmailRecipientError) as caught:
            cipher.decrypt(ciphertext=encrypted.ciphertext, **kwargs)

        assert RECIPIENT not in str(caught.value)
        assert encrypted.ciphertext not in str(caught.value)


def test_errors_and_logs_do_not_contain_recipient_or_ciphertext(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cipher = TransactionalEmailRecipientCipher(keyring(1))

    with caplog.at_level(logging.DEBUG):
        encrypted = cipher.encrypt(recipient=RECIPIENT, delivery_id=1)

        with pytest.raises(TransactionalEmailRecipientError) as caught:
            cipher.decrypt(ciphertext="garbage", key_version=1, delivery_id=1)

    assert RECIPIENT not in str(caught.value)
    assert RECIPIENT not in caplog.text
    assert encrypted.ciphertext not in caplog.text


@pytest.mark.parametrize("recipient", ["", "   ", "a" * 321])
def test_invalid_recipient_is_rejected_without_echo(recipient: str) -> None:
    with pytest.raises(TransactionalEmailRecipientError) as caught:
        TransactionalEmailRecipientCipher(keyring(1)).encrypt(recipient=recipient, delivery_id=1)

    assert recipient.strip() == "" or recipient not in str(caught.value)


def test_keyring_is_independent_of_provider_credential_keyring() -> None:
    assert not issubclass(TransactionalEmailKeyring, credential_keyring.ProviderCredentialKeyring)
    assert not issubclass(
        TransactionalEmailKeyringConfigurationError,
        credential_keyring.ProviderCredentialKeyringConfigurationError,
    )

    provider_key = fernet_key()
    settings = Settings(
        _env_file=None,
        **REQUIRED,
        provider_credential_keys=SecretStr(json.dumps({"1": provider_key})),
    )

    assert settings.provider_credential_keyring().configured is True
    assert settings.transactional_email_keyring().configured is False
