from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from app.core.settings import Settings
from app.scripts.sync_scheduler import (
    _wait_for_target,
    next_scheduled_time,
    run_scheduler,
)


def create_settings(
    *,
    enabled: bool,
) -> Settings:
    return Settings(
        app_env="test",
        app_debug=True,
        app_secret_key=SecretStr("x" * 48),
        frontend_origin="http://localhost:5173",
        trusted_origins="http://localhost:5173",
        session_cookie_name="fantasy_session",
        session_cookie_secure=False,
        espn_s2=SecretStr("test-espn-s2"),
        espn_swid=SecretStr("{test-swid}"),
        database_url=("postgresql+psycopg://user:password@database:5432/fantasy"),
        sync_scheduler_enabled=enabled,
        sync_schedule_timezone="America/Chicago",
        sync_schedule_weekday=2,
        sync_schedule_hour=6,
        sync_schedule_minute=0,
        sync_scheduler_poll_seconds=5,
    )


def test_disabled_scheduler_refuses_to_start() -> None:
    settings = create_settings(enabled=False)

    with pytest.raises(
        SystemExit,
        match="scheduler is disabled",
    ):
        run_scheduler(settings)


def test_next_scheduled_time_uses_settings() -> None:
    settings = create_settings(enabled=False)
    now = datetime(
        2026,
        11,
        6,
        17,
        0,
        tzinfo=UTC,
    )

    target = next_scheduled_time(
        settings,
        now=now,
    )

    assert target == datetime(
        2026,
        11,
        11,
        6,
        0,
        tzinfo=ZoneInfo("America/Chicago"),
    )


def test_zero_delay_returns_without_shutdown() -> None:
    assert (
        _wait_for_target(
            delay_seconds=0.0,
            poll_seconds=5,
        )
        is False
    )


def test_small_delay_completes() -> None:
    assert (
        _wait_for_target(
            delay_seconds=0.001,
            poll_seconds=5,
        )
        is False
    )
