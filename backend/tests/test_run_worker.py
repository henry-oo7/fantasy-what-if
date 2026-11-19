from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.scripts.run_worker import (
    SHUTDOWN_EVENT,
    build_worker_id,
    run_worker,
)


def settings(
    *,
    enabled: bool,
    worker_id: str | None = None,
):
    return SimpleNamespace(
        background_worker_enabled=enabled,
        background_worker_id=worker_id,
        background_worker_lease_seconds=300,
        background_worker_heartbeat_seconds=60,
        background_worker_poll_seconds=5,
        background_worker_database_backoff_seconds=10,
    )


def test_configured_worker_id_is_preserved() -> None:
    assert (
        build_worker_id(
            " worker-production-01 ",
            hostname="ignored",
            process_id=999,
        )
        == "worker-production-01"
    )


def test_worker_id_uses_hostname_and_process_id() -> None:
    assert (
        build_worker_id(
            None,
            hostname="backend-worker",
            process_id=1234,
        )
        == "backend-worker:1234"
    )


def test_blank_hostname_uses_safe_fallback() -> None:
    assert (
        build_worker_id(
            None,
            hostname=" ",
            process_id=1234,
        )
        == "unknown-host:1234"
    )


def test_generated_worker_id_respects_database_limit() -> None:
    worker_id = build_worker_id(
        None,
        hostname="x" * 300,
        process_id=1234,
    )

    assert len(worker_id) == 255


def test_disabled_worker_refuses_to_start() -> None:
    with pytest.raises(
        SystemExit,
        match="Background worker is disabled",
    ):
        run_worker(
            settings(
                enabled=False,
            )
        )


def test_enabled_worker_builds_handlers_and_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler_registry = {
        "handler": object(),
    }
    handler_builder = MagicMock(
        return_value=handler_registry,
    )
    runner = MagicMock()
    signal_installer = MagicMock()

    monkeypatch.setattr(
        "app.scripts.run_worker.build_worker_id",
        MagicMock(
            return_value="worker-test-01",
        ),
    )
    monkeypatch.setattr(
        ("app.scripts.run_worker.build_background_job_handlers"),
        handler_builder,
    )
    monkeypatch.setattr(
        ("app.scripts.run_worker.run_background_job_worker"),
        runner,
    )
    monkeypatch.setattr(
        ("app.scripts.run_worker.install_signal_handlers"),
        signal_installer,
    )

    SHUTDOWN_EVENT.clear()

    run_worker(
        settings(
            enabled=True,
            worker_id="worker-test-01",
        )
    )

    signal_installer.assert_called_once()
    handler_builder.assert_called_once()

    runner.assert_called_once()
    call = runner.call_args.kwargs

    assert call["handlers"] is handler_registry
    assert call["shutdown_event"] is SHUTDOWN_EVENT

    configuration = call["configuration"]

    assert configuration.worker_id == "worker-test-01"
    assert configuration.lease_duration.total_seconds() == 300
    assert configuration.heartbeat_interval.total_seconds() == 60
    assert configuration.poll_seconds == 5
    assert configuration.database_backoff_seconds == 10
