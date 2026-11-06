from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def calculate_next_sync_time(
    *,
    now: datetime,
    timezone_name: str,
    weekday: int,
    hour: int,
    minute: int,
) -> datetime:
    timezone = ZoneInfo(timezone_name)

    if now.tzinfo is None:
        raise ValueError("Current time must be timezone-aware.")

    local_now = now.astimezone(timezone)

    days_until_target = (weekday - local_now.weekday()) % 7

    candidate = local_now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    ) + timedelta(days=days_until_target)

    if candidate <= local_now:
        candidate += timedelta(days=7)

    return candidate


def seconds_until(
    *,
    now: datetime,
    target: datetime,
) -> float:
    if now.tzinfo is None or target.tzinfo is None:
        raise ValueError("Both times must be timezone-aware.")

    return max(
        (target - now).total_seconds(),
        0.0,
    )
