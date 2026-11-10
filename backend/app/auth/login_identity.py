"""Privacy-preserving login client identity."""

from __future__ import annotations

from ipaddress import ip_address, ip_network
from secrets import token_urlsafe

from app.auth.security import hmac_identifier

LOGIN_BROWSER_ID_BYTES = 32


def generate_login_browser_id() -> str:
    """Generate an opaque browser identifier."""

    return token_urlsafe(LOGIN_BROWSER_ID_BYTES)


def normalize_network_prefix(
    source_address: str | None,
) -> str:
    """Normalize an address to a privacy-limited network prefix."""

    if source_address is None:
        return "unknown"

    normalized = source_address.strip()

    if not normalized:
        return "unknown"

    try:
        address = ip_address(normalized)
    except ValueError:
        return "unknown"

    prefix_length = 24 if address.version == 4 else 64

    return str(
        ip_network(
            f"{address}/{prefix_length}",
            strict=False,
        )
    )


def browser_identifier_hash(
    browser_id: str,
    *,
    secret: str,
) -> str:
    """Hash an opaque browser identifier with the app secret."""

    return hmac_identifier(
        browser_id,
        secret=secret,
        namespace="login-browser",
    )


def network_prefix_hash(
    source_address: str | None,
    *,
    secret: str,
) -> str:
    """Hash the normalized source network prefix."""

    return hmac_identifier(
        normalize_network_prefix(source_address),
        secret=secret,
        namespace="login-network",
    )


def client_bucket_hash(
    *,
    browser_id: str,
    source_address: str | None,
    secret: str,
) -> str:
    """Combine browser and network signals into one throttle key."""

    browser_hash = browser_identifier_hash(
        browser_id,
        secret=secret,
    )
    network_hash = network_prefix_hash(
        source_address,
        secret=secret,
    )

    return hmac_identifier(
        f"{browser_hash}:{network_hash}",
        secret=secret,
        namespace="login-client-bucket",
    )
