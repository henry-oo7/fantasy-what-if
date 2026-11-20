from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from app.core.settings import Settings
from app.db.platform_enums import (
    BackgroundJobStatus,
)
from app.scripts.sync_scheduler import (
    _wait_for_target,
    enqueue_scheduled_batch,
    enqueue_workspace_now,
    next_scheduled_time,
    run_scheduler,
)
from app.services.scheduled_synchronization_jobs import (
    ScheduledSynchronizationBatch,
    ScheduledSynchronizationJob,
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
        database_url=("postgresql+psycopg://user:password@database:5432/fantasy"),
        sync_scheduler_enabled=enabled,
        sync_schedule_timezone="America/Chicago",
        sync_schedule_weekday=2,
        sync_schedule_hour=6,
        sync_schedule_minute=0,
        sync_scheduler_poll_seconds=5,
    )


def scheduled_for() -> datetime:
    return datetime(
        2026,
        11,
        25,
        12,
        0,
        tzinfo=UTC,
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


def test_scheduled_batch_is_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = scheduled_for()
    batch = ScheduledSynchronizationBatch(
        scheduled_for=timestamp,
        jobs=(
            ScheduledSynchronizationJob(
                job_id=42,
                workspace_id=1,
                season_id=1,
                status=(BackgroundJobStatus.PENDING),
                existing=False,
            ),
        ),
    )
    enqueue = MagicMock(
        return_value=batch,
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
        ("app.scripts.sync_scheduler.enqueue_scheduled_synchronization_batch"),
        enqueue,
    )

    result = enqueue_scheduled_batch(
        scheduled_for=timestamp,
    )

    assert result is batch
    enqueue.assert_called_once_with(
        database_session,
        scheduled_for=timestamp,
    )
    database_session.commit.assert_called_once()


def test_batch_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_context = MagicMock()
    session_context.__enter__.return_value = MagicMock()

    monkeypatch.setattr(
        "app.scripts.sync_scheduler.SessionFactory",
        MagicMock(
            return_value=session_context,
        ),
    )
    monkeypatch.setattr(
        ("app.scripts.sync_scheduler.enqueue_scheduled_synchronization_batch"),
        MagicMock(
            side_effect=RuntimeError("Synthetic database failure."),
        ),
    )

    result = enqueue_scheduled_batch(
        scheduled_for=scheduled_for(),
    )

    assert result is None


def test_immediate_job_resolves_active_season_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = scheduled_for()
    database_session = MagicMock()
    session_context = MagicMock()
    session_context.__enter__.return_value = database_session
    resolve_context = MagicMock(
        return_value=SimpleNamespace(
            season_id=17,
        )
    )
    accepted = ScheduledSynchronizationJob(
        job_id=84,
        workspace_id=42,
        season_id=17,
        status=BackgroundJobStatus.PENDING,
        existing=False,
    )
    enqueue = MagicMock(
        return_value=accepted,
    )

    monkeypatch.setattr(
        "app.scripts.sync_scheduler.SessionFactory",
        MagicMock(
            return_value=session_context,
        ),
    )
    monkeypatch.setattr(
        ("app.scripts.sync_scheduler.resolve_active_season_context"),
        resolve_context,
    )
    monkeypatch.setattr(
        ("app.scripts.sync_scheduler.enqueue_scheduled_synchronization"),
        enqueue,
    )

    result = enqueue_workspace_now(
        workspace_id=42,
        scheduled_for=timestamp,
    )

    assert result is accepted
    resolve_context.assert_called_once_with(
        session=database_session,
        workspace_id=42,
    )

    call = enqueue.call_args

    assert call.args == (database_session,)
    assert call.kwargs["target"].workspace_id == 42
    assert call.kwargs["target"].season_id == 17
    assert call.kwargs["scheduled_for"] == timestamp
    database_session.commit.assert_called_once()


def test_immediate_job_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_context = MagicMock()
    session_context.__enter__.return_value = MagicMock()

    monkeypatch.setattr(
        "app.scripts.sync_scheduler.SessionFactory",
        MagicMock(
            return_value=session_context,
        ),
    )
    monkeypatch.setattr(
        ("app.scripts.sync_scheduler.resolve_active_season_context"),
        MagicMock(
            side_effect=RuntimeError("Unknown workspace."),
        ),
    )

    result = enqueue_workspace_now(
        workspace_id=999_999,
        scheduled_for=scheduled_for(),
    )

    assert result is None
