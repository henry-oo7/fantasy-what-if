"""League-Season synchronization lifecycle (ADR 0010).

The Season row is the runtime authority. Every function here mutates a
Season the caller has already locked (``SELECT ... FOR UPDATE``) inside its
own short transaction; none commits.

Transitions::

    enqueue (new job)      any state except syncing   -> queued
    attempt start          any state                  -> syncing
    successful persistence syncing                    -> fresh
    attempt failure        syncing / queued / NULL    -> stale  (prior data)
                                                      -> failed (no prior data)
    lease lost, retrying   syncing                    -> queued
    lease lost, exhausted  syncing                    -> stale / failed

Rules:

- ``data_version`` increments only on successful canonical persistence and
  is the stale-write marker: persistence commits only when it still equals
  the value captured when the attempt started.
- ``last_successful_sync_at`` and the compatibility ``last_synced_at`` change
  only on success. ``last_synced_at`` is retained for current workspace
  responses and is planned for retirement after the identity cutover.
- ``last_sync_attempt_at`` records each authoritative attempt start.
- ``last_sync_failure_code`` holds a bounded safe code only.
- A NULL ``sync_state`` is a legacy row. It is never read as ``fresh``; the
  first transition replaces it, and "prior data exists" is inferred only from
  a recorded successful synchronization, never from the row alone.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.db.models import Season
from app.db.platform_enums import SeasonSyncState

GENERIC_SYNC_FAILURE_CODE = "sync_failed"
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def safe_failure_code(code: str | None) -> str:
    """Bound a failure code to the Season column contract."""

    return code if code is not None and _SAFE_CODE.fullmatch(code) else GENERIC_SYNC_FAILURE_CODE


def has_successful_data(season: Season) -> bool:
    """Whether a successful canonical dataset was ever recorded for the Season."""

    return (
        season.data_version > 0
        or season.last_successful_sync_at is not None
        or season.last_synced_at is not None
    )


def mark_queued(season: Season) -> None:
    if season.sync_state is not SeasonSyncState.SYNCING:
        season.sync_state = SeasonSyncState.QUEUED


def mark_requeued(season: Season) -> None:
    """A syncing attempt lost its worker and a retry is scheduled."""

    season.sync_state = SeasonSyncState.QUEUED


def mark_syncing(season: Season, *, attempt_at: datetime) -> None:
    season.sync_state = SeasonSyncState.SYNCING
    season.last_sync_attempt_at = attempt_at


def mark_succeeded(season: Season, *, completed_at: datetime) -> None:
    season.data_version += 1
    season.sync_state = SeasonSyncState.FRESH
    season.last_successful_sync_at = completed_at
    season.last_synced_at = completed_at
    season.last_sync_failure_code = None


def mark_failed(season: Season, *, failure_code: str | None) -> None:
    season.sync_state = (
        SeasonSyncState.STALE if has_successful_data(season) else SeasonSyncState.FAILED
    )
    season.last_sync_failure_code = safe_failure_code(failure_code)
