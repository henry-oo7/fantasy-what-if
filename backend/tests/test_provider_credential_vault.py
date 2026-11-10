import json

import pytest
from cryptography.fernet import Fernet

from app.db.platform_enums import FantasyProvider
from app.providers.credential_vault import (
    CREDENTIAL_SCHEMA_VERSION,
    ProviderCredentialConfigurationError,
    ProviderCredentialDecryptionError,
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials


def make_key() -> str:
    return Fernet.generate_key().decode("ascii")


def make_credentials() -> ProviderCredentials:
    return ProviderCredentials(
        values={
            "league_id": "529250970",
            "season": "2026",
            "espn_s2": "private-cookie",
            "swid": "{private-swid}",
        }
    )


def test_credentials_round_trip() -> None:
    key = make_key()
    vault = ProviderCredentialVault(
        keys={
            1: key,
        }
    )

    token = vault.encrypt(
        provider=FantasyProvider.ESPN,
        credentials=make_credentials(),
        key_version=1,
    )

    assert "private-cookie" not in token
    assert "{private-swid}" not in token

    envelope = vault.decrypt(
        encrypted_credentials=token,
        key_version=1,
        expected_provider=FantasyProvider.ESPN,
    )

    assert envelope.schema_version == CREDENTIAL_SCHEMA_VERSION
    assert envelope.provider is FantasyProvider.ESPN
    assert dict(envelope.credentials.values) == {
        "espn_s2": "private-cookie",
        "league_id": "529250970",
        "season": "2026",
        "swid": "{private-swid}",
    }


def test_encryption_uses_deterministic_plaintext() -> None:
    key = make_key()
    vault = ProviderCredentialVault(
        keys={
            1: key,
        }
    )

    token = vault.encrypt(
        provider=FantasyProvider.ESPN,
        credentials=make_credentials(),
        key_version=1,
    )

    plaintext = Fernet(key.encode("ascii")).decrypt(token.encode("ascii"))

    assert plaintext == json.dumps(
        {
            "credentials": {
                "espn_s2": "private-cookie",
                "league_id": "529250970",
                "season": "2026",
                "swid": "{private-swid}",
            },
            "provider": "espn",
            "schema_version": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def test_wrong_key_is_rejected_safely() -> None:
    writer = ProviderCredentialVault(
        keys={
            1: make_key(),
        }
    )
    reader = ProviderCredentialVault(
        keys={
            1: make_key(),
        }
    )

    token = writer.encrypt(
        provider=FantasyProvider.ESPN,
        credentials=make_credentials(),
        key_version=1,
    )

    with pytest.raises(
        ProviderCredentialDecryptionError,
        match="could not be decrypted",
    ) as error:
        reader.decrypt(
            encrypted_credentials=token,
            key_version=1,
            expected_provider=(FantasyProvider.ESPN),
        )

    assert "private-cookie" not in str(error.value)


def test_corrupted_token_is_rejected_safely() -> None:
    vault = ProviderCredentialVault(
        keys={
            1: make_key(),
        }
    )

    with pytest.raises(
        ProviderCredentialDecryptionError,
        match="could not be decrypted",
    ):
        vault.decrypt(
            encrypted_credentials=("corrupted-ciphertext"),
            key_version=1,
            expected_provider=(FantasyProvider.ESPN),
        )


def test_provider_mismatch_is_rejected() -> None:
    vault = ProviderCredentialVault(
        keys={
            1: make_key(),
        }
    )

    token = vault.encrypt(
        provider=FantasyProvider.ESPN,
        credentials=make_credentials(),
        key_version=1,
    )

    with pytest.raises(
        ProviderCredentialDecryptionError,
        match="do not match",
    ):
        vault.decrypt(
            encrypted_credentials=token,
            key_version=1,
            expected_provider=(FantasyProvider.SLEEPER),
        )


def test_missing_key_version_is_rejected() -> None:
    vault = ProviderCredentialVault(
        keys={
            1: make_key(),
        }
    )

    with pytest.raises(
        ProviderCredentialConfigurationError,
        match="unavailable",
    ):
        vault.encrypt(
            provider=FantasyProvider.ESPN,
            credentials=make_credentials(),
            key_version=2,
        )


def test_key_rotation_preserves_credentials() -> None:
    first_key = make_key()
    second_key = make_key()

    vault = ProviderCredentialVault(
        keys={
            1: first_key,
            2: second_key,
        }
    )

    original = vault.encrypt(
        provider=FantasyProvider.ESPN,
        credentials=make_credentials(),
        key_version=1,
    )

    rotated = vault.rotate(
        encrypted_credentials=original,
        current_key_version=1,
        new_key_version=2,
        expected_provider=FantasyProvider.ESPN,
    )

    assert rotated != original

    envelope = vault.decrypt(
        encrypted_credentials=rotated,
        key_version=2,
        expected_provider=FantasyProvider.ESPN,
    )

    assert dict(envelope.credentials.values) == dict(make_credentials().values)

    with pytest.raises(ProviderCredentialDecryptionError):
        vault.decrypt(
            encrypted_credentials=rotated,
            key_version=1,
            expected_provider=(FantasyProvider.ESPN),
        )


@pytest.mark.parametrize(
    "keys",
    [
        {},
        {
            0: "invalid",
        },
        {
            1: "",
        },
        {
            1: "not-a-fernet-key",
        },
    ],
)
def test_invalid_vault_configuration_is_rejected(
    keys: dict[int, str],
) -> None:
    with pytest.raises(ProviderCredentialConfigurationError):
        ProviderCredentialVault(keys=keys)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {
            "": "value",
        },
        {
            "key": "",
        },
        {
            "key": 123,
        },
    ],
)
def test_invalid_credentials_are_rejected(
    values: dict,
) -> None:
    vault = ProviderCredentialVault(
        keys={
            1: make_key(),
        }
    )

    with pytest.raises(ProviderCredentialConfigurationError):
        vault.encrypt(
            provider=FantasyProvider.ESPN,
            credentials=ProviderCredentials(values=values),
            key_version=1,
        )


def test_supported_versions_are_stable() -> None:
    vault = ProviderCredentialVault(
        keys={
            3: make_key(),
            1: make_key(),
            2: make_key(),
        }
    )

    assert vault.supported_key_versions == (
        1,
        2,
        3,
    )
