"""Normalization for provider-neutral transactional-email settings.

Error messages name the setting, never the rejected value, so a mistyped
secret or address is not echoed into logs or startup output.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Deliberately conservative: one local part, one dotted domain, no spaces,
# quotes, angle brackets, or control characters (header-injection safe).
_SENDER_ADDRESS = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
    r"@(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)
_SENDER_NAME_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f\"<>\\,;:@]")


def normalize_sender_address(value: str) -> str:
    normalized = value.strip()

    if len(normalized) > 254 or not _SENDER_ADDRESS.fullmatch(normalized):
        raise ValueError("TRANSACTIONAL_EMAIL_FROM_ADDRESS must be a valid email address.")

    local, domain = normalized.rsplit("@", 1)

    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValueError("TRANSACTIONAL_EMAIL_FROM_ADDRESS must be a valid email address.")

    return f"{local}@{domain.lower()}"


def normalize_sender_name(value: str) -> str:
    normalized = value.strip()

    if not normalized or len(normalized) > 100 or _SENDER_NAME_FORBIDDEN.search(normalized):
        raise ValueError(
            "TRANSACTIONAL_EMAIL_FROM_NAME must be 1 to 100 characters without "
            "quotes, angle brackets, separators, or control characters."
        )

    return normalized


def normalize_public_base_url(value: str, *, allow_http: bool) -> str:
    """Return an absolute origin with no trailing slash.

    Credentials, query, fragment, and any path other than ``/`` are rejected:
    ADR 0009 requires the value to be an origin. HTTPS is required unless
    ``allow_http`` (development and test only).
    """

    message = (
        "TRANSACTIONAL_EMAIL_PUBLIC_BASE_URL must be an absolute origin without "
        "credentials, path, query, or fragment."
    )

    try:
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(message) from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(message)

    if parsed.scheme != "https" and not allow_http:
        raise ValueError(
            "TRANSACTIONAL_EMAIL_PUBLIC_BASE_URL must use HTTPS outside development and test."
        )

    host = hostname.lower()

    if ":" in host:
        host = f"[{host}]"

    netloc = host if port is None else f"{host}:{port}"

    return f"{parsed.scheme}://{netloc}"
