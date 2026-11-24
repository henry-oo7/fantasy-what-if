"""Resolve the client address used for throttling and audit metadata.

Trust boundary: the API never reads CF-Connecting-IP, Forwarded, X-Real-IP or
X-Forwarded-For itself. Uvicorn rewrites ``request.client`` from
X-Forwarded-For only when the immediate peer is listed in
``FORWARDED_ALLOW_IPS`` (the frontend Nginx container in production), and
Nginx replaces that header with the address it validated. Every other peer is
used as-is. This module only normalizes the already-resolved address.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request


def normalize_ip_address(value: str | None) -> str | None:
    """Return a canonical IP string, or None when the value is not one address."""

    if value is None:
        return None

    candidate = value.strip()

    if not candidate or any(c in candidate for c in ", %"):
        return None

    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None

    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.compressed

    return address.compressed


def resolve_client_ip(request: Request) -> str | None:
    """Return the normalized client IP for the request, or None if unknown."""

    if request.client is None:
        return None

    return normalize_ip_address(request.client.host)
