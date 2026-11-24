"""Direct, bounded Resend HTTP transport (ADR 0009).

One POST per call with no adapter retries and no redirects. Only the
minimum success field (``id``) and, on failure, the documented error
``name`` are parsed from a size-bounded body; the raw body is never
retained, logged, or attached to an exception.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from pydantic import SecretStr

from app.notifications.email_provider import (
    EmailAcceptance,
    EmailFailureCategory,
    EmailProviderError,
    OutgoingEmail,
)

RESEND_EMAILS_ENDPOINT = "https://api.resend.com/emails"
MAXIMUM_RESPONSE_BYTES = 16 * 1024
DEFAULT_RATE_LIMIT_WAIT = timedelta(seconds=60)
DEFAULT_MAXIMUM_DEFERRAL = timedelta(hours=1)
QUOTA_BOUNDARY_MARGIN = timedelta(minutes=1)
_PROVIDER_MESSAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_ERROR_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_AUTHENTICATION_NAMES = frozenset(
    {
        "missing_api_key",
        "restricted_api_key",
        "suspended_api_key",
        "invalid_api_key",
        "invalid_permission",
    }
)
_RETRYABLE_CONFLICT_NAMES = frozenset(
    {
        "concurrent_idempotent_requests",
        "resource_locked",
        # A changed body under a reused key; the next attempt uses a new key.
        "invalid_idempotent_request",
    }
)


class _ResponseTooLarge(Exception):
    pass


class ResendTransport:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 7.0,
        maximum_deferral: timedelta = DEFAULT_MAXIMUM_DEFERRAL,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise ValueError("A transactional-email API key is required.")

        if maximum_deferral <= timedelta(0):
            raise ValueError("maximum_deferral must be positive.")

        self._api_key = api_key
        self._maximum_deferral = maximum_deferral
        self._now = now
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=connect_timeout_seconds,
                read=read_timeout_seconds,
                write=read_timeout_seconds,
                pool=connect_timeout_seconds,
            ),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def send(self, message: OutgoingEmail) -> EmailAcceptance:
        request = self._client.build_request(
            "POST",
            RESEND_EMAILS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Idempotency-Key": message.idempotency_key,
                "User-Agent": "fantasy-what-if-transactional-email",
            },
            content=json.dumps(
                {
                    "from": f"{message.sender_name} <{message.sender_address}>",
                    "to": [message.recipient],
                    "subject": message.subject,
                    "text": message.text_body,
                    "html": message.html_body,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )

        try:
            response = self._client.send(request, stream=True)
        except httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout:
            # No connection was established, so the provider never saw the request.
            raise EmailProviderError(EmailFailureCategory.PROVIDER_UNAVAILABLE) from None
        except httpx.TransportError:
            # The request may have been transmitted; the outcome is unknown.
            raise EmailProviderError(EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN) from None
        except Exception:
            raise EmailProviderError(EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID) from None

        try:
            status_code = response.status_code
            retry_after = response.headers.get("retry-after")

            try:
                body = _read_bounded(response)
            except _ResponseTooLarge:
                body = None
            except httpx.HTTPError:
                if 200 <= status_code < 300:
                    # Accepted status, unreadable body: the outcome is uncertain.
                    raise EmailProviderError(
                        EmailFailureCategory.PROVIDER_RESPONSE_INVALID
                    ) from None
                body = b""
        finally:
            response.close()

        if 200 <= status_code < 300:
            return EmailAcceptance(provider_message_id=_parse_message_id(body))

        raise self._classify_failure(status_code, body, retry_after)

    def _classify_failure(
        self,
        status_code: int,
        body: bytes | None,
        retry_after: str | None,
    ) -> EmailProviderError:
        name = _parse_error_name(body)

        if status_code == 429:
            now = self._now()

            if name == "daily_quota_exceeded":
                return EmailProviderError(
                    EmailFailureCategory.PROVIDER_QUOTA_EXHAUSTED,
                    retry_not_before=next_utc_day(now) + QUOTA_BOUNDARY_MARGIN,
                )

            if name == "monthly_quota_exceeded":
                return EmailProviderError(
                    EmailFailureCategory.PROVIDER_QUOTA_EXHAUSTED,
                    retry_not_before=next_utc_month(now) + QUOTA_BOUNDARY_MARGIN,
                )

            return EmailProviderError(
                EmailFailureCategory.PROVIDER_RATE_LIMITED,
                retry_not_before=now
                + parse_retry_after(
                    retry_after,
                    now=now,
                    maximum=self._maximum_deferral,
                ),
            )

        if status_code >= 500:
            return EmailProviderError(EmailFailureCategory.PROVIDER_UNAVAILABLE)

        if status_code == 401 or (status_code == 403 and name in _AUTHENTICATION_NAMES):
            return EmailProviderError(EmailFailureCategory.PROVIDER_AUTHENTICATION_FAILED)

        if status_code == 403:
            # Documented 403 validation_error cases are sender-side: unverified
            # domain, testing-only restriction, or account sending limits.
            return EmailProviderError(EmailFailureCategory.SENDER_NOT_VERIFIED)

        if status_code == 409 and name in _RETRYABLE_CONFLICT_NAMES:
            return EmailProviderError(EmailFailureCategory.PROVIDER_UNAVAILABLE)

        if status_code == 400 and name == "validation_error":
            # Sender, subject, and bodies are validated locally; the only
            # externally supplied field is the recipient address.
            return EmailProviderError(EmailFailureCategory.RECIPIENT_INVALID)

        return EmailProviderError(EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID)


def _read_bounded(response: httpx.Response) -> bytes:
    declared = response.headers.get("content-length")

    if declared is not None and declared.isdigit() and int(declared) > MAXIMUM_RESPONSE_BYTES:
        raise _ResponseTooLarge

    collected = bytearray()

    for chunk in response.iter_bytes():
        collected.extend(chunk)

        if len(collected) > MAXIMUM_RESPONSE_BYTES:
            raise _ResponseTooLarge

    return bytes(collected)


def _load_object(body: bytes | None) -> dict[str, object] | None:
    if not body:
        return None

    try:
        value = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return None

    return value if isinstance(value, dict) else None


def _parse_message_id(body: bytes | None) -> str:
    payload = _load_object(body)
    message_id = payload.get("id") if payload is not None else None

    if not isinstance(message_id, str) or not _PROVIDER_MESSAGE_ID.fullmatch(message_id):
        raise EmailProviderError(EmailFailureCategory.PROVIDER_RESPONSE_INVALID)

    return message_id


def _parse_error_name(body: bytes | None) -> str | None:
    payload = _load_object(body)
    name = payload.get("name") if payload is not None else None

    if isinstance(name, str) and _ERROR_NAME.fullmatch(name):
        return name

    return None


def parse_retry_after(
    value: str | None,
    *,
    now: datetime,
    maximum: timedelta,
    default: timedelta = DEFAULT_RATE_LIMIT_WAIT,
) -> timedelta:
    """Return a wait clamped to [1 second, maximum]; invalid input uses default."""

    wait = default

    if value is not None:
        text = value.strip()

        if text.isdigit() and len(text) <= 9:
            wait = timedelta(seconds=int(text))
        elif 0 < len(text) <= 64:
            try:
                retry_at = parsedate_to_datetime(text)
            except TypeError, ValueError, IndexError, OverflowError:
                retry_at = None

            if retry_at is not None and retry_at.tzinfo is not None:
                wait = retry_at - now

    return min(max(wait, timedelta(seconds=1)), maximum)


def next_utc_day(now: datetime) -> datetime:
    current = now.astimezone(UTC)

    return datetime(current.year, current.month, current.day, tzinfo=UTC) + timedelta(days=1)


def next_utc_month(now: datetime) -> datetime:
    current = now.astimezone(UTC)

    if current.month == 12:
        return datetime(current.year + 1, 1, 1, tzinfo=UTC)

    return datetime(current.year, current.month + 1, 1, tzinfo=UTC)
