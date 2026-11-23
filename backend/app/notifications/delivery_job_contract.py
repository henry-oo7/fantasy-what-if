"""Contract for transactional-email BackgroundJob payloads.

The delivery row is the only authority. A job payload names the delivery
and nothing else: no recipient, token, link, slug, user ID, event type,
provider key, subject, or body.
"""

from __future__ import annotations

from typing import Any

TRANSACTIONAL_EMAIL_JOB_PAYLOAD_VERSION = 1
TRANSACTIONAL_EMAIL_JOB_IDEMPOTENCY_PREFIX = "transactional-email-delivery:"
_PAYLOAD_KEYS = frozenset({"version", "delivery_id"})


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def build_transactional_email_job_payload(delivery_id: int) -> dict[str, Any]:
    if not _is_positive_int(delivery_id):
        raise ValueError("delivery_id must be a positive integer.")

    return {
        "version": TRANSACTIONAL_EMAIL_JOB_PAYLOAD_VERSION,
        "delivery_id": delivery_id,
    }


def transactional_email_job_idempotency_key(delivery_id: int) -> str:
    if not _is_positive_int(delivery_id):
        raise ValueError("delivery_id must be a positive integer.")

    return f"{TRANSACTIONAL_EMAIL_JOB_IDEMPOTENCY_PREFIX}{delivery_id}"


def validate_transactional_email_job_payload(payload: object) -> int:
    """Return the delivery ID of an exactly-shaped payload, or raise ValueError."""

    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError(
            "A transactional-email job payload must contain exactly version and delivery_id."
        )

    if payload["version"] != TRANSACTIONAL_EMAIL_JOB_PAYLOAD_VERSION or isinstance(
        payload["version"], bool
    ):
        raise ValueError("Unsupported transactional-email job payload version.")

    delivery_id = payload["delivery_id"]

    if not _is_positive_int(delivery_id):
        raise ValueError("delivery_id must be a positive integer.")

    return delivery_id
