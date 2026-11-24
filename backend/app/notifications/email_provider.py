"""Provider-neutral transactional-email transport contract.

An adapter sends one already-rendered message and either returns the
provider message ID or raises :class:`EmailProviderError` with a stable
category. Errors never carry the API key, recipient, token, body, raw
provider response, or authorization header.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class EmailFailureDisposition(StrEnum):
    RETRYABLE = "retryable"
    """Spend a normal job attempt and retry with a new delivery attempt."""

    DEFERRABLE = "deferrable"
    """Wait without spending a job attempt."""

    PERMANENT = "permanent"
    """Never retry."""


class EmailFailureCategory(StrEnum):
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_QUOTA_EXHAUSTED = "provider_quota_exhausted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT_UNKNOWN = "provider_timeout_unknown"
    PROVIDER_CONFIGURATION_INVALID = "provider_configuration_invalid"
    SENDER_NOT_VERIFIED = "sender_not_verified"
    RECIPIENT_INVALID = "recipient_invalid"
    PROVIDER_AUTHENTICATION_FAILED = "provider_authentication_failed"
    PROVIDER_RESPONSE_INVALID = "provider_response_invalid"

    @property
    def disposition(self) -> EmailFailureDisposition:
        return _DISPOSITIONS[self]


_DISPOSITIONS: dict[EmailFailureCategory, EmailFailureDisposition] = {
    EmailFailureCategory.PROVIDER_RATE_LIMITED: EmailFailureDisposition.DEFERRABLE,
    EmailFailureCategory.PROVIDER_QUOTA_EXHAUSTED: EmailFailureDisposition.DEFERRABLE,
    EmailFailureCategory.PROVIDER_UNAVAILABLE: EmailFailureDisposition.RETRYABLE,
    # The request may have been accepted; a retry is a new attempt and may
    # duplicate delivery. The first attempt is never treated as failed.
    EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN: EmailFailureDisposition.RETRYABLE,
    # A malformed success body is an uncertain outcome as well.
    EmailFailureCategory.PROVIDER_RESPONSE_INVALID: EmailFailureDisposition.RETRYABLE,
    EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID: EmailFailureDisposition.PERMANENT,
    EmailFailureCategory.SENDER_NOT_VERIFIED: EmailFailureDisposition.PERMANENT,
    EmailFailureCategory.RECIPIENT_INVALID: EmailFailureDisposition.PERMANENT,
    EmailFailureCategory.PROVIDER_AUTHENTICATION_FAILED: EmailFailureDisposition.PERMANENT,
}


class EmailProviderError(RuntimeError):
    """A classified, secret-free provider failure.

    ``retry_not_before`` is set only for deferrable categories when the
    provider supplied (or implied) a safe wait boundary.
    """

    def __init__(
        self,
        category: EmailFailureCategory,
        *,
        retry_not_before: datetime | None = None,
    ) -> None:
        self.category = category
        self.retry_not_before = retry_not_before

        super().__init__(f"Transactional email provider failure: {category.value}.")

    @property
    def disposition(self) -> EmailFailureDisposition:
        return self.category.disposition


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    """One rendered message held only in process memory."""

    recipient: str = field(repr=False)
    sender_address: str
    sender_name: str
    subject: str = field(repr=False)
    text_body: str = field(repr=False)
    html_body: str = field(repr=False)
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class EmailAcceptance:
    provider_message_id: str


class TransactionalEmailTransport(Protocol):
    def send(self, message: OutgoingEmail) -> EmailAcceptance:
        """Send once, without retries, or raise :class:`EmailProviderError`."""
        ...


def provider_idempotency_key(*, delivery_id: int, attempt: int) -> str:
    """Derive the provider key only from the delivery ID and attempt number."""

    if (
        isinstance(delivery_id, bool)
        or isinstance(attempt, bool)
        or not isinstance(delivery_id, int)
        or not isinstance(attempt, int)
        or delivery_id < 1
        or attempt < 1
    ):
        raise ValueError("delivery_id and attempt must be positive integers.")

    return f"{delivery_id}:{attempt}"
