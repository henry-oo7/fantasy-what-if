from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.sync_schedule import (
    calculate_next_sync_time,
    seconds_until,
)

CHICAGO = ZoneInfo("America/Chicago")


def test_next_sync_is_upcoming_wednesday() -> None:
    now = datetime(
        2026,
        11,
        6,
        17,
        0,
        tzinfo=UTC,
    )

    result = calculate_next_sync_time(
        now=now,
        timezone_name="America/Chicago",
        weekday=2,
        hour=6,
        minute=0,
    )

    assert result == datetime(
        2026,
        11,
        11,
        6,
        0,
        tzinfo=CHICAGO,
    )


def test_same_day_before_schedule_uses_today() -> None:
    now = datetime(
        2026,
        11,
        11,
        5,
        30,
        tzinfo=CHICAGO,
    )

    result = calculate_next_sync_time(
        now=now,
        timezone_name="America/Chicago",
        weekday=2,
        hour=6,
        minute=0,
    )

    assert result == datetime(
        2026,
        11,
        11,
        6,
        0,
        tzinfo=CHICAGO,
    )


def test_same_day_after_schedule_uses_next_week() -> None:
    now = datetime(
        2026,
        11,
        11,
        6,
        1,
        tzinfo=CHICAGO,
    )

    result = calculate_next_sync_time(
        now=now,
        timezone_name="America/Chicago",
        weekday=2,
        hour=6,
        minute=0,
    )

    assert result == datetime(
        2026,
        11,
        18,
        6,
        0,
        tzinfo=CHICAGO,
    )


def test_exact_schedule_time_uses_next_week() -> None:
    now = datetime(
        2026,
        11,
        11,
        6,
        0,
        tzinfo=CHICAGO,
    )

    result = calculate_next_sync_time(
        now=now,
        timezone_name="America/Chicago",
        weekday=2,
        hour=6,
        minute=0,
    )

    assert result == datetime(
        2026,
        11,
        18,
        6,
        0,
        tzinfo=CHICAGO,
    )


def test_seconds_until_never_returns_negative() -> None:
    now = datetime(
        2026,
        11,
        11,
        7,
        0,
        tzinfo=CHICAGO,
    )
    target = datetime(
        2026,
        11,
        11,
        6,
        0,
        tzinfo=CHICAGO,
    )

    assert (
        seconds_until(
            now=now,
            target=target,
        )
        == 0.0
    )


def test_naive_current_time_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        calculate_next_sync_time(
            now=datetime(2026, 11, 6, 12, 0),
            timezone_name="America/Chicago",
            weekday=2,
            hour=6,
            minute=0,
        )
