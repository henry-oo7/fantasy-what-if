"""Versioned provider credential encryption-key configuration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from cryptography.fernet import Fernet
from pydantic import SecretStr


class ProviderCredentialKeyringConfigurationError(ValueError):
    """Provider credential keyring configuration is invalid."""


@dataclass(frozen=True, slots=True)
class ProviderCredentialKeyring:
    """Validated versioned provider credential encryption keys."""

    keys: Mapping[int, str] = field(repr=False)
    current_version: int = 1

    def __post_init__(self) -> None:
        normalized_keys = _validate_keys(self.keys)

        if self.current_version < 1:
            raise ProviderCredentialKeyringConfigurationError(
                "The current provider credential key version must be positive."
            )

        if normalized_keys and self.current_version not in normalized_keys:
            raise ProviderCredentialKeyringConfigurationError(
                "The current provider credential key version is not configured."
            )

        object.__setattr__(
            self,
            "keys",
            MappingProxyType(normalized_keys),
        )

    @property
    def configured(self) -> bool:
        """Return whether credential encryption keys are configured."""

        return bool(self.keys)

    @property
    def current_key(self) -> str:
        """Return the key used for new credential encryption."""

        key = self.keys.get(self.current_version)

        if key is None:
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential encryption is not configured."
            )

        return key

    def key_for_version(
        self,
        version: int,
    ) -> str:
        """Return the key assigned to one persisted key version."""

        key = self.keys.get(version)

        if key is None:
            raise ProviderCredentialKeyringConfigurationError(
                f"Provider credential key version {version} is not configured."
            )

        return key


def build_provider_credential_keyring(
    *,
    serialized_keys: SecretStr | None,
    current_version: int,
) -> ProviderCredentialKeyring:
    """Build a keyring from versioned SaaS settings."""

    serialized_value = _optional_secret_value(serialized_keys)

    if serialized_value is None:
        return ProviderCredentialKeyring(
            keys={},
            current_version=current_version,
        )

    keys = _parse_serialized_keys(serialized_value)

    return ProviderCredentialKeyring(
        keys=keys,
        current_version=current_version,
    )


def _optional_secret_value(
    value: SecretStr | None,
) -> str | None:
    if value is None:
        return None

    normalized = value.get_secret_value().strip()

    return normalized or None


def _parse_serialized_keys(
    serialized_keys: str,
) -> dict[int, str]:
    try:
        parsed = json.loads(serialized_keys)
    except json.JSONDecodeError as exc:
        raise ProviderCredentialKeyringConfigurationError(
            "PROVIDER_CREDENTIAL_KEYS must be a valid JSON object."
        ) from exc

    if not isinstance(parsed, dict) or not parsed:
        raise ProviderCredentialKeyringConfigurationError(
            "PROVIDER_CREDENTIAL_KEYS must be a non-empty JSON object."
        )

    keys: dict[int, str] = {}

    for raw_version, raw_key in parsed.items():
        if not isinstance(raw_version, str):
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential key versions must be JSON object keys."
            )

        normalized_version = raw_version.strip()

        if not normalized_version.isdecimal() or normalized_version.startswith("0"):
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential key versions must be positive integers."
            )

        version = int(normalized_version)

        if version < 1:
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential key versions must be positive integers."
            )

        if version in keys:
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential key versions must be unique."
            )

        if not isinstance(raw_key, str):
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential encryption keys must be strings."
            )

        keys[version] = raw_key

    return keys


def _validate_keys(
    keys: Mapping[int, str],
) -> dict[int, str]:
    normalized: dict[int, str] = {}

    for version, key in keys.items():
        if not isinstance(version, int) or isinstance(version, bool):
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential key versions must be integers."
            )

        if version < 1:
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential key versions must be positive."
            )

        if not isinstance(key, str):
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential encryption keys must be strings."
            )

        normalized_key = key.strip()

        if not normalized_key:
            raise ProviderCredentialKeyringConfigurationError(
                "Provider credential encryption keys must not be empty."
            )

        try:
            Fernet(normalized_key.encode("ascii"))
        except (
            TypeError,
            ValueError,
            UnicodeEncodeError,
        ) as exc:
            raise ProviderCredentialKeyringConfigurationError(
                f"Provider credential key version {version} must be a valid Fernet key."
            ) from exc

        normalized[version] = normalized_key

    return dict(sorted(normalized.items()))
