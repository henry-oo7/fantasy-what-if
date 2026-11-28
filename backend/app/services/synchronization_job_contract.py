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


# --------------------------------------------------------------------------
# Personal access reverification (ADR 0010)
# --------------------------------------------------------------------------

ACCESS_REVERIFICATION_PAYLOAD_VERSION = 1
ACCESS_REVERIFICATION_IDEMPOTENCY_PREFIX = "access-reverification:"


class InvalidAccessReverificationPayloadError(ValueError):
    """A reverification payload is not exactly {version, user_id, season_id}."""


def _require_positive_id(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidAccessReverificationPayloadError(f"{name} must be a positive integer.")

    return value


def access_reverification_idempotency_key(user_id: int, season_id: int) -> str:
    """The single active-job key for one user's access to one Season."""

    return (
        f"{ACCESS_REVERIFICATION_IDEMPOTENCY_PREFIX}"
        f"{_require_positive_id(user_id, 'user_id')}:{_require_positive_id(season_id, 'season_id')}"
    )


def build_access_reverification_payload(user_id: int, season_id: int) -> dict[str, int]:
    """The complete payload: no workspace, email, connection, credential or flag."""

    return {
        "version": ACCESS_REVERIFICATION_PAYLOAD_VERSION,
        "user_id": _require_positive_id(user_id, "user_id"),
        "season_id": _require_positive_id(season_id, "season_id"),
    }


def validate_access_reverification_payload(
    payload: Mapping[str, Any], *, payload_version: int
) -> tuple[int, int]:
    """Return ``(user_id, season_id)``, or raise when the payload is not exact."""

    if (
        isinstance(payload_version, bool)
        or payload_version != ACCESS_REVERIFICATION_PAYLOAD_VERSION
        or set(payload) != {"version", "user_id", "season_id"}
        or isinstance(payload["version"], bool)
        or payload["version"] != ACCESS_REVERIFICATION_PAYLOAD_VERSION
    ):
        raise InvalidAccessReverificationPayloadError("The reverification payload is invalid.")

    return (
        _require_positive_id(payload["user_id"], "user_id"),
        _require_positive_id(payload["season_id"], "season_id"),
    )


ORPHAN_LEAGUE_PURGE_PAYLOAD_VERSION = 1
ORPHAN_LEAGUE_PURGE_IDEMPOTENCY_PREFIX = "orphan-league-purge:"


class InvalidOrphanLeaguePurgePayloadError(ValueError):
    """A purge payload is not exactly {version, league_id}."""


def _require_league_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidOrphanLeaguePurgePayloadError("league_id must be a positive integer.")

    return value


def orphan_league_purge_idempotency_key(league_id: int) -> str:
    """The single active-job key for one League's purge."""

    return f"{ORPHAN_LEAGUE_PURGE_IDEMPOTENCY_PREFIX}{_require_league_id(league_id)}"


def build_orphan_league_purge_payload(league_id: int) -> dict[str, int]:
    """The complete payload: no user, email, credential, connection or workspace."""

    return {
        "version": ORPHAN_LEAGUE_PURGE_PAYLOAD_VERSION,
        "league_id": _require_league_id(league_id),
    }


def validate_orphan_league_purge_payload(
    payload: Mapping[str, Any], *, payload_version: int
) -> int:
    """Return the League ID, or raise when the payload is not exact."""

    if (
        isinstance(payload_version, bool)
        or payload_version != ORPHAN_LEAGUE_PURGE_PAYLOAD_VERSION
        or set(payload) != {"version", "league_id"}
        or isinstance(payload["version"], bool)
        or payload["version"] != ORPHAN_LEAGUE_PURGE_PAYLOAD_VERSION
    ):
        raise InvalidOrphanLeaguePurgePayloadError("The purge payload is invalid.")

    return _require_league_id(payload["league_id"])
