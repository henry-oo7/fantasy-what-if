"""Deletion-email idempotency keys and reminder timing (ADR 0009, FR-20).

The functions are pure; the suite still runs through the guarded test
database workflow with every other backend test.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.workspace_deletion_email import (
    REMINDER_LEAD_TIME,
    deletion_canceled_key,
    deletion_completed_key,
    deletion_reminder_key,
    deletion_requested_key,
    generation_marker,
    reminder_time,
)

MOMENT = datetime(2026, 11, 24, 12, 0, 0, 123456, tzinfo=UTC)
MARKER = "20261124T120000.123456Z"
PLUS_FIVE = timezone(timedelta(hours=5, minutes=30))

BUILDERS = [
    deletion_requested_key,
    deletion_reminder_key,
    deletion_canceled_key,
    deletion_completed_key,
]


def test_exact_key_formats() -> None:
    prefix = "identity-email:workspace_deletion_"

    assert deletion_requested_key(7, MOMENT) == f"{prefix}requested:workspace:7:request:{MARKER}"
    assert deletion_reminder_key(7, MOMENT) == f"{prefix}reminder:workspace:7:scheduled:{MARKER}"
    assert deletion_canceled_key(7, MOMENT) == f"{prefix}canceled:workspace:7:request:{MARKER}"
    assert (
        deletion_completed_key(7, MOMENT)
        == f"{prefix}completed:former-workspace:7:scheduled:{MARKER}"
    )


def test_literal_requested_key_example() -> None:
    assert deletion_requested_key(7, MOMENT) == (
        "identity-email:workspace_deletion_requested:workspace:7:request:20261124T120000.123456Z"
    )


def test_marker_has_microsecond_precision_for_whole_seconds() -> None:
    assert generation_marker(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "20260102T030405.000000Z"


@pytest.mark.parametrize("builder", BUILDERS)
def test_non_utc_aware_timestamps_normalize_to_the_same_marker(builder) -> None:
    local = MOMENT.astimezone(PLUS_FIVE)

    assert local.utcoffset() != timedelta(0)
    assert builder(7, local) == builder(7, MOMENT)


@pytest.mark.parametrize("builder", BUILDERS)
def test_naive_datetimes_are_rejected(builder) -> None:
    with pytest.raises(ValueError):
        builder(7, MOMENT.replace(tzinfo=None))


def test_generation_marker_rejects_non_datetimes() -> None:
    with pytest.raises(ValueError):
        generation_marker("2026-11-24T12:00:00Z")  # type: ignore[arg-type]


@pytest.mark.parametrize("builder", BUILDERS)
@pytest.mark.parametrize("workspace_id", [0, -1, True, "7"])
def test_invalid_workspace_ids_are_rejected(builder, workspace_id) -> None:
    with pytest.raises(ValueError):
        builder(workspace_id, MOMENT)


@pytest.mark.parametrize("builder", BUILDERS)
def test_keys_are_deterministic_safe_and_bounded(builder) -> None:
    key = builder(2_000_000_000, MOMENT)

    assert key == builder(2_000_000_000, MOMENT)
    assert "@" not in key
    assert not any(character.isspace() for character in key)
    assert len(key) <= 255
    assert key != builder(2_000_000_001, MOMENT)
    assert key != builder(2_000_000_000, MOMENT + timedelta(microseconds=1))


def test_each_event_has_a_distinct_key_for_the_same_inputs() -> None:
    assert len({builder(7, MOMENT) for builder in BUILDERS}) == len(BUILDERS)


def test_reminder_is_seven_days_before_the_scheduled_deletion() -> None:
    requested = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    scheduled = requested + timedelta(days=30)

    assert timedelta(days=7) == REMINDER_LEAD_TIME
    assert reminder_time(requested_at=requested, scheduled_for=scheduled) == (
        scheduled - timedelta(days=7)
    )


@pytest.mark.parametrize("grace", [timedelta(days=7), timedelta(days=3), timedelta(0)])
def test_no_reminder_when_schedule_is_within_seven_days_of_the_request(
    grace: timedelta,
) -> None:
    requested = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

    assert reminder_time(requested_at=requested, scheduled_for=requested + grace) is None


def test_reminder_exists_just_beyond_seven_days() -> None:
    requested = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    scheduled = requested + timedelta(days=7, microseconds=1)

    assert reminder_time(requested_at=requested, scheduled_for=scheduled) == (
        requested + timedelta(microseconds=1)
    )
