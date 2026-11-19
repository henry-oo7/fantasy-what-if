"""Shared contract for durable season-synchronization jobs."""

from __future__ import annotations

from typing import Any

SYNCHRONIZATION_JOB_PAYLOAD_VERSION = 1

PROHIBITED_SYNCHRONIZATION_PAYLOAD_NAMES = frozenset(
    {
        "authorization",
        "encrypted_credentials",
        "espn_s2",
        "oauth_access_token",
        "oauth_refresh_token",
        "password",
        "provider_credentials",
        "session_token",
        "swid",
    }
)


def build_synchronization_job_payload(
    *,
    season_id: int,
    full_sync: bool,
    trigger: str,
) -> dict[str, Any]:
    """Build the secret-free payload shared by synchronization producers."""

    if isinstance(season_id, bool) or not isinstance(season_id, int) or season_id < 1:
        raise ValueError("season_id must be a positive integer.")

    if not isinstance(full_sync, bool):
        raise ValueError("full_sync must be a boolean.")

    normalized_trigger = trigger.strip()
    if not normalized_trigger:
        raise ValueError("trigger must not be empty.")

    return {
        "season_id": season_id,
        "full_sync": full_sync,
        "trigger": normalized_trigger,
    }


def synchronization_payload_contains_prohibited_names(
    payload: dict[str, Any],
) -> bool:
    """Return whether a synchronization payload contains secret-bearing names."""

    normalized_names = {str(name).strip().casefold() for name in payload}
    return bool(normalized_names & PROHIBITED_SYNCHRONIZATION_PAYLOAD_NAMES)
