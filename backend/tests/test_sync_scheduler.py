from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from app.core.settings import Settings
from app.scripts.sync_scheduler import (
    _wait_for_target,
    list_active_workspace_ids,
    next_scheduled_time,
    run_incremental_sync,
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


def test_active_workspace_ids_are_returned_in_query_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_session = MagicMock()
    database_session.scalars.return_value.all.return_value = [
        3,
        8,
        21,
    ]

    session_context = MagicMock()
    session_context.__enter__.return_value = database_session

    session_factory = MagicMock(
        return_value=session_context,
    )

    monkeypatch.setattr(
        "app.scripts.sync_scheduler.SessionFactory",
        session_factory,
    )

    assert list_active_workspace_ids() == (
        3,
        8,
        21,
    )

    database_session.scalars.assert_called_once()


def test_incremental_sync_forwards_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = create_settings(
        enabled=True,
    )
    expected_result = SimpleNamespace(
        current_week=9,
        player_weeks=(7, 8),
        teams_upserted=12,
        matchups_upserted=72,
        player_week_scores_upserted=400,
    )

    synchronize = MagicMock(
        return_value=expected_result,
    )

    database_session = MagicMock()
    session_context = MagicMock()
    session_context.__enter__.return_value = database_session

    monkeypatch.setattr(
        "app.scripts.sync_scheduler.SessionFactory",
        MagicMock(
            return_value=session_context,
        ),
    )
    monkeypatch.setattr(
        "app.scripts.sync_scheduler.synchronize_active_season",
        synchronize,
    )

    result = run_incremental_sync(
        settings,
        workspace_id=42,
    )

    assert result is expected_result

    synchronize.assert_called_once_with(
        session=database_session,
        workspace_id=42,
        settings=settings,
        full_sync=False,
    )


def test_workspace_sync_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = create_settings(
        enabled=True,
    )

    database_session = MagicMock()
    session_context = MagicMock()
    session_context.__enter__.return_value = database_session

    monkeypatch.setattr(
        "app.scripts.sync_scheduler.SessionFactory",
        MagicMock(
            return_value=session_context,
        ),
    )
    monkeypatch.setattr(
        "app.scripts.sync_scheduler.synchronize_active_season",
        MagicMock(
            side_effect=RuntimeError("Synthetic workspace failure."),
        ),
    )

    result = run_incremental_sync(
        settings,
        workspace_id=42,
    )

    assert result is None
