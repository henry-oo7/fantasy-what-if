"""Shared contract for durable season-synchronization jobs."""

from __future__ import annotations

from collections.abc import Mapping
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


def synchronization_payload_contains_prohibited_names(
    payload: dict[str, Any],
) -> bool:
    """Return whether a synchronization payload contains secret-bearing names."""

    normalized_names = {str(name).strip().casefold() for name in payload}
    return bool(normalized_names & PROHIBITED_SYNCHRONIZATION_PAYLOAD_NAMES)


# --------------------------------------------------------------------------
# League-Season synchronization (ADR 0010)
# --------------------------------------------------------------------------

SEASON_SYNC_PAYLOAD_VERSION = 1
SEASON_SYNC_IDEMPOTENCY_PREFIX = "league-season-sync:"


class InvalidSeasonSyncPayloadError(ValueError):
    """A League-Season synchronization payload is not exactly {version, season_id}."""


def _require_season_id(season_id: object) -> int:
    if isinstance(season_id, bool) or not isinstance(season_id, int) or season_id < 1:
        raise InvalidSeasonSyncPayloadError("season_id must be a positive integer.")

    return season_id


def season_sync_idempotency_key(season_id: int) -> str:
    """The single active-job key for one Season, shared by every caller."""

    return f"{SEASON_SYNC_IDEMPOTENCY_PREFIX}{_require_season_id(season_id)}"


def build_season_sync_payload(season_id: int) -> dict[str, int]:
    """The complete Season-sync payload: no workspace, user, connection or flags."""

    return {"version": SEASON_SYNC_PAYLOAD_VERSION, "season_id": _require_season_id(season_id)}


def validate_season_sync_payload(payload: Mapping[str, Any], *, payload_version: int) -> int:
    """Return the payload Season ID, or raise when the payload is not exact."""

    if (
        isinstance(payload_version, bool)
        or payload_version != SEASON_SYNC_PAYLOAD_VERSION
        or set(payload) != {"version", "season_id"}
        or isinstance(payload["version"], bool)
        or payload["version"] != SEASON_SYNC_PAYLOAD_VERSION
        or not isinstance(payload["version"], int)
    ):
        raise InvalidSeasonSyncPayloadError("The Season synchronization payload is invalid.")

    return _require_season_id(payload["season_id"])
