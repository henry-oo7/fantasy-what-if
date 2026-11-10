"""Authenticated encryption for provider credentials."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.fernet import (
    Fernet,
    InvalidToken,
)

from app.db.platform_enums import FantasyProvider
from app.providers.domain import ProviderCredentials

CREDENTIAL_SCHEMA_VERSION = 1


class ProviderCredentialError(RuntimeError):
    """Base error for provider credential storage."""


class ProviderCredentialConfigurationError(ProviderCredentialError):
    """Credential encryption keys are unavailable or invalid."""


class ProviderCredentialDecryptionError(ProviderCredentialError):
    """Stored provider credentials could not be decrypted safely."""


@dataclass(frozen=True, slots=True)
class ProviderCredentialEnvelope:
    """Decrypted, versioned provider credential payload."""

    schema_version: int
    provider: FantasyProvider
    credentials: ProviderCredentials


class ProviderCredentialVault:
    """Encrypt and decrypt provider credentials with versioned keys."""

    def __init__(
        self,
        *,
        keys: Mapping[int, str],
    ) -> None:
        if not keys:
            raise ProviderCredentialConfigurationError(
                "At least one provider credential key is required."
            )

        ciphers: dict[int, Fernet] = {}

        for version, key in keys.items():
            if version < 1:
                raise ProviderCredentialConfigurationError(
                    "Credential key versions must be positive."
                )

            normalized_key = key.strip()

            if not normalized_key:
                raise ProviderCredentialConfigurationError(
                    "Provider credential keys must not be empty."
                )

            try:
                ciphers[version] = Fernet(normalized_key.encode("ascii"))
            except (TypeError, ValueError) as exc:
                raise ProviderCredentialConfigurationError(
                    "A provider credential key is invalid."
                ) from exc

        self._ciphers = ciphers

    @property
    def supported_key_versions(
        self,
    ) -> tuple[int, ...]:
        """Return configured key versions in stable order."""

        return tuple(sorted(self._ciphers))

    def encrypt(
        self,
        *,
        provider: FantasyProvider,
        credentials: ProviderCredentials,
        key_version: int,
    ) -> str:
        """Encrypt one provider-bound credential payload."""

        cipher = self._cipher_for_version(key_version)

        normalized_values = self._normalize_values(credentials.values)

        payload = {
            "credentials": normalized_values,
            "provider": provider.value,
            "schema_version": (CREDENTIAL_SCHEMA_VERSION),
        }

        serialized = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        return cipher.encrypt(serialized).decode("ascii")

    def decrypt(
        self,
        *,
        encrypted_credentials: str,
        key_version: int,
        expected_provider: FantasyProvider,
    ) -> ProviderCredentialEnvelope:
        """Decrypt and validate one provider-bound payload."""

        cipher = self._cipher_for_version(key_version)

        token = encrypted_credentials.strip()

        if not token:
            raise ProviderCredentialDecryptionError("Stored provider credentials are empty.")

        try:
            serialized = cipher.decrypt(token.encode("ascii"))
        except (
            InvalidToken,
            UnicodeEncodeError,
        ) as exc:
            raise ProviderCredentialDecryptionError(
                "Stored provider credentials could not be decrypted."
            ) from exc

        try:
            payload = json.loads(serialized.decode("utf-8"))
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ProviderCredentialDecryptionError(
                "Stored provider credentials are malformed."
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderCredentialDecryptionError("Stored provider credentials are malformed.")

        schema_version = payload.get("schema_version")

        if schema_version != CREDENTIAL_SCHEMA_VERSION:
            raise ProviderCredentialDecryptionError(
                "Stored provider credentials use an unsupported schema version."
            )

        provider_value = payload.get("provider")

        try:
            provider = FantasyProvider(provider_value)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ProviderCredentialDecryptionError(
                "Stored provider credentials contain an invalid provider."
            ) from exc

        if provider is not expected_provider:
            raise ProviderCredentialDecryptionError(
                "Stored provider credentials do not match the provider connection."
            )

        values = payload.get("credentials")

        if not isinstance(values, dict):
            raise ProviderCredentialDecryptionError("Stored provider credentials are malformed.")

        normalized_values = self._normalize_values(
            values,
            error_type=(ProviderCredentialDecryptionError),
        )

        return ProviderCredentialEnvelope(
            schema_version=schema_version,
            provider=provider,
            credentials=ProviderCredentials(values=normalized_values),
        )

    def rotate(
        self,
        *,
        encrypted_credentials: str,
        current_key_version: int,
        new_key_version: int,
        expected_provider: FantasyProvider,
    ) -> str:
        """Re-encrypt credentials using another configured key."""

        envelope = self.decrypt(
            encrypted_credentials=(encrypted_credentials),
            key_version=current_key_version,
            expected_provider=expected_provider,
        )

        return self.encrypt(
            provider=envelope.provider,
            credentials=envelope.credentials,
            key_version=new_key_version,
        )

    def _cipher_for_version(
        self,
        key_version: int,
    ) -> Fernet:
        cipher = self._ciphers.get(key_version)

        if cipher is None:
            raise ProviderCredentialConfigurationError(
                "The requested provider credential key version is unavailable."
            )

        return cipher

    @staticmethod
    def _normalize_values(
        values: Mapping[object, object],
        *,
        error_type: type[ProviderCredentialError] = ProviderCredentialConfigurationError,
    ) -> dict[str, str]:
        normalized: dict[str, str] = {}

        for key, value in values.items():
            if not isinstance(key, str):
                raise error_type("Provider credential names must be strings.")

            normalized_key = key.strip()

            if not normalized_key:
                raise error_type("Provider credential names must not be empty.")

            if not isinstance(value, str):
                raise error_type("Provider credential values must be strings.")

            normalized_value = value.strip()

            if not normalized_value:
                raise error_type("Provider credential values must not be empty.")

            normalized[normalized_key] = normalized_value

        if not normalized:
            raise error_type("Provider credentials must not be empty.")

        return normalized
