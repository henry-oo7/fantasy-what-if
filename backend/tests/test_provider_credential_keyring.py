import json

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
    ProviderCredentialKeyringConfigurationError,
    build_provider_credential_keyring,
)


def make_key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_versioned_keyring_selects_current_key() -> None:
    first_key = make_key()
    second_key = make_key()

    keyring = build_provider_credential_keyring(
        serialized_keys=SecretStr(
            json.dumps(
                {
                    "1": first_key,
                    "2": second_key,
                }
            )
        ),
        current_version=2,
    )

    assert keyring.configured is True
    assert keyring.current_version == 2
    assert dict(keyring.keys) == {
        1: first_key,
        2: second_key,
    }
    assert keyring.current_key == second_key
    assert keyring.key_for_version(1) == first_key
    assert keyring.key_for_version(2) == second_key


def test_unconfigured_keyring_is_explicit() -> None:
    keyring = build_provider_credential_keyring(
        serialized_keys=None,
        current_version=1,
    )

    assert keyring.configured is False
    assert dict(keyring.keys) == {}

    with pytest.raises(
        ProviderCredentialKeyringConfigurationError,
        match="not configured",
    ):
        _ = keyring.current_key


def test_keyring_mapping_is_immutable() -> None:
    keyring = ProviderCredentialKeyring(
        keys={1: make_key()},
        current_version=1,
    )

    with pytest.raises(TypeError):
        keyring.keys[2] = make_key()  # type: ignore[index]


def test_current_version_must_exist() -> None:
    with pytest.raises(
        ProviderCredentialKeyringConfigurationError,
        match="current.*not configured",
    ):
        build_provider_credential_keyring(
            serialized_keys=SecretStr(json.dumps({"1": make_key()})),
            current_version=2,
        )


@pytest.mark.parametrize(
    "serialized",
    [
        "not-json",
        "[]",
        "{}",
        json.dumps({"0": make_key()}),
        json.dumps({"01": make_key()}),
        json.dumps({"one": make_key()}),
        json.dumps({"1": 123}),
    ],
)
def test_invalid_serialized_keyring_is_rejected(
    serialized: str,
) -> None:
    with pytest.raises(
        ProviderCredentialKeyringConfigurationError,
    ):
        build_provider_credential_keyring(
            serialized_keys=SecretStr(serialized),
            current_version=1,
        )


@pytest.mark.parametrize(
    "keys",
    [
        {0: make_key()},
        {-1: make_key()},
        {1: ""},
        {1: "not-a-fernet-key"},
        {True: make_key()},
    ],
)
def test_invalid_key_mapping_is_rejected(
    keys,
) -> None:
    with pytest.raises(
        ProviderCredentialKeyringConfigurationError,
    ):
        ProviderCredentialKeyring(
            keys=keys,
            current_version=1,
        )


def test_missing_historical_version_is_safe() -> None:
    keyring = ProviderCredentialKeyring(
        keys={2: make_key()},
        current_version=2,
    )

    with pytest.raises(
        ProviderCredentialKeyringConfigurationError,
        match="version 1 is not configured",
    ):
        keyring.key_for_version(1)


def test_keyring_repr_does_not_expose_keys() -> None:
    key = make_key()
    keyring = ProviderCredentialKeyring(
        keys={1: key},
        current_version=1,
    )

    assert key not in repr(keyring)
    assert repr(keyring) == ("ProviderCredentialKeyring(current_version=1)")
