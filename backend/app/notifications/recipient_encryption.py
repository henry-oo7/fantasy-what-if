"""Versioned encryption for unscoped transactional-email recipients.

This boundary is independent of the provider-credential keyring and the
session keys. Rotating one never requires the others. Errors never carry
recipient values, ciphertext, or key material, and nothing here logs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

RECIPIENT_ENVELOPE_VERSION = 1


class TransactionalEmailKeyringConfigurationError(ValueError):
    """The transactional-email keyring configuration is invalid."""


class TransactionalEmailRecipientError(RuntimeError):
    """A recipient could not be encrypted or decrypted safely."""


@dataclass(frozen=True, slots=True)
class TransactionalEmailKeyring:
    """Validated versioned recipient-encryption keys."""

    keys: Mapping[int, str] = field(repr=False)
    current_version: int = 1

    def __post_init__(self) -> None:
        normalized = _validate_keys(self.keys)

        if self.current_version < 1:
            raise TransactionalEmailKeyringConfigurationError(
                "The current transactional-email key version must be positive."
            )

        if normalized and self.current_version not in normalized:
            raise TransactionalEmailKeyringConfigurationError(
                "The current transactional-email key version is not configured."
            )

        object.__setattr__(self, "keys", MappingProxyType(normalized))

    @property
    def configured(self) -> bool:
        return bool(self.keys)

    def key_for_version(self, version: int) -> str:
        key = self.keys.get(version)

        if key is None:
            raise TransactionalEmailKeyringConfigurationError(
                f"Transactional-email key version {version} is not configured."
            )

        return key


@dataclass(frozen=True, slots=True)
class EncryptedRecipient:
    """Ciphertext plus the key version needed to decrypt it."""

    ciphertext: str = field(repr=False)
    key_version: int


class TransactionalEmailRecipientCipher:
    """Encrypt one recipient, bound to its delivery ID, with versioned keys."""

    def __init__(self, keyring: TransactionalEmailKeyring) -> None:
        if not keyring.configured:
            raise TransactionalEmailKeyringConfigurationError(
                "Transactional-email recipient encryption is not configured."
            )

        self._keyring = keyring

    def encrypt(self, *, recipient: str, delivery_id: int) -> EncryptedRecipient:
        normalized = _normalize_recipient(recipient)
        _require_delivery_id(delivery_id)
        version = self._keyring.current_version
        cipher = Fernet(self._keyring.key_for_version(version).encode("ascii"))
        envelope = json.dumps(
            {
                "v": RECIPIENT_ENVELOPE_VERSION,
                "delivery_id": delivery_id,
                "recipient": normalized,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        return EncryptedRecipient(
            ciphertext=cipher.encrypt(envelope).decode("ascii"),
            key_version=version,
        )

    def decrypt(self, *, ciphertext: str, key_version: int, delivery_id: int) -> str:
        _require_delivery_id(delivery_id)

        try:
            cipher = Fernet(self._keyring.key_for_version(key_version).encode("ascii"))
        except TransactionalEmailKeyringConfigurationError as exc:
            raise TransactionalEmailRecipientError(
                "The recipient key version is not configured."
            ) from exc

        try:
            envelope = json.loads(cipher.decrypt(ciphertext.strip().encode("ascii")))
            recipient = envelope["recipient"]
            bound_id = envelope["delivery_id"]
            version = envelope["v"]
        except (InvalidToken, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise TransactionalEmailRecipientError(
                "The stored recipient could not be decrypted."
            ) from exc

        if (
            version != RECIPIENT_ENVELOPE_VERSION
            or bound_id != delivery_id
            or not isinstance(recipient, str)
        ):
            raise TransactionalEmailRecipientError("The stored recipient could not be decrypted.")

        return recipient


def build_transactional_email_keyring(
    *,
    serialized_keys: SecretStr | None,
    current_version: int,
) -> TransactionalEmailKeyring:
    """Build a keyring from TRANSACTIONAL_EMAIL_KEYS settings."""

    raw = serialized_keys.get_secret_value().strip() if serialized_keys is not None else ""

    if not raw:
        return TransactionalEmailKeyring(keys={}, current_version=current_version)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TransactionalEmailKeyringConfigurationError(
            "TRANSACTIONAL_EMAIL_KEYS must be a valid JSON object."
        ) from exc

    if not isinstance(parsed, dict) or not parsed:
        raise TransactionalEmailKeyringConfigurationError(
            "TRANSACTIONAL_EMAIL_KEYS must be a non-empty JSON object."
        )

    keys: dict[int, str] = {}

    for raw_version, raw_key in parsed.items():
        version_text = str(raw_version).strip()

        if not version_text.isdecimal() or version_text.startswith("0"):
            raise TransactionalEmailKeyringConfigurationError(
                "Transactional-email key versions must be positive integers."
            )

        version = int(version_text)

        if version in keys:
            raise TransactionalEmailKeyringConfigurationError(
                "Transactional-email key versions must be unique."
            )

        if not isinstance(raw_key, str):
            raise TransactionalEmailKeyringConfigurationError(
                "Transactional-email encryption keys must be strings."
            )

        keys[version] = raw_key

    return TransactionalEmailKeyring(keys=keys, current_version=current_version)


def _normalize_recipient(recipient: str) -> str:
    if not isinstance(recipient, str):
        raise TransactionalEmailRecipientError("The recipient must be a string.")

    normalized = recipient.strip()

    if not normalized or len(normalized) > 320:
        raise TransactionalEmailRecipientError("The recipient is empty or too long.")

    return normalized


def _require_delivery_id(delivery_id: int) -> None:
    if isinstance(delivery_id, bool) or not isinstance(delivery_id, int) or delivery_id < 1:
        raise TransactionalEmailRecipientError("delivery_id must be a positive integer.")


def _validate_keys(keys: Mapping[int, str]) -> dict[int, str]:
    normalized: dict[int, str] = {}

    for version, key in keys.items():
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise TransactionalEmailKeyringConfigurationError(
                "Transactional-email key versions must be positive integers."
            )

        if not isinstance(key, str) or not key.strip():
            raise TransactionalEmailKeyringConfigurationError(
                "Transactional-email encryption keys must be non-empty strings."
            )

        stripped = key.strip()

        try:
            Fernet(stripped.encode("ascii"))
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise TransactionalEmailKeyringConfigurationError(
                f"Transactional-email key version {version} must be a valid Fernet key."
            ) from exc

        normalized[version] = stripped

    return dict(sorted(normalized.items()))
