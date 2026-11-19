from datetime import timedelta
from threading import Event
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.services.background_job_runner import (
    BackgroundJobRunnerConfiguration,
    run_background_job_worker,
)
from app.services.background_job_worker import (
    BackgroundJobExecutionResult,
)


class RecordingEvent:
    def __init__(
        self,
        *,
        stop_after_waits: int,
    ) -> None:
        self.stop_after_waits = stop_after_waits
        self.wait_calls: list[float] = []
        self._stopped = False

    def is_set(self) -> bool:
        return self._stopped

    def wait(
        self,
        timeout: float,
    ) -> bool:
        self.wait_calls.append(timeout)

        if len(self.wait_calls) >= self.stop_after_waits:
            self._stopped = True

        return self._stopped


def configuration() -> BackgroundJobRunnerConfiguration:
    return BackgroundJobRunnerConfiguration(
        worker_id="worker-test-01",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=1),
        poll_seconds=2,
        database_backoff_seconds=11,
    )


def idle_result() -> BackgroundJobExecutionResult:
    return BackgroundJobExecutionResult(
        job_id=None,
        claimed=False,
        succeeded=False,
        retry_scheduled=False,
        failed=False,
    )


def completed_result(
    job_id: int = 42,
) -> BackgroundJobExecutionResult:
    return BackgroundJobExecutionResult(
        job_id=job_id,
        claimed=True,
        succeeded=True,
        retry_scheduled=False,
        failed=False,
    )


def test_idle_worker_waits_for_poll_interval() -> None:
    shutdown = RecordingEvent(
        stop_after_waits=1,
    )
    execute_one = MagicMock(
        return_value=idle_result(),
    )

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,  # type: ignore[arg-type]
        execute_one=execute_one,
    )

    execute_one.assert_called_once()
    assert shutdown.wait_calls == [2]


def test_claimed_job_continues_without_idle_wait() -> None:
    shutdown = RecordingEvent(
        stop_after_waits=1,
    )
    calls = 0

    def execute_one(
        **_kwargs,
    ) -> BackgroundJobExecutionResult:
        nonlocal calls
        calls += 1

        if calls == 1:
            return completed_result()

        return idle_result()

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,  # type: ignore[arg-type]
        execute_one=execute_one,
    )

    assert calls == 2
    assert shutdown.wait_calls == [2]


def test_database_failure_uses_backoff_interval() -> None:
    shutdown = RecordingEvent(
        stop_after_waits=1,
    )

    def execute_one(
        **_kwargs,
    ) -> BackgroundJobExecutionResult:
        raise OperationalError(
            "SELECT 1",
            {},
            RuntimeError("database unavailable"),
        )

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,  # type: ignore[arg-type]
        execute_one=execute_one,
    )

    assert shutdown.wait_calls == [11]


def test_unexpected_iteration_failure_uses_backoff() -> None:
    shutdown = RecordingEvent(
        stop_after_waits=1,
    )

    def execute_one(
        **_kwargs,
    ) -> BackgroundJobExecutionResult:
        raise RuntimeError("outcome persistence failed")

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,  # type: ignore[arg-type]
        execute_one=execute_one,
    )

    assert shutdown.wait_calls == [11]


def test_preexisting_shutdown_claims_no_jobs() -> None:
    shutdown = Event()
    shutdown.set()
    execute_one = MagicMock()

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,
        execute_one=execute_one,
    )

    execute_one.assert_not_called()


def test_shutdown_during_idle_wait_exits_cleanly() -> None:
    shutdown = Event()

    def execute_one(
        **_kwargs,
    ) -> BackgroundJobExecutionResult:
        shutdown.set()
        return idle_result()

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,
        execute_one=execute_one,
    )

    assert shutdown.is_set()


def test_runner_forwards_runtime_contract() -> None:
    shutdown = RecordingEvent(
        stop_after_waits=1,
    )
    captured: dict[str, object] = {}

    def execute_one(
        **kwargs,
    ) -> BackgroundJobExecutionResult:
        captured.update(kwargs)
        return idle_result()

    session_factory = MagicMock()
    handlers: dict = {}

    run_background_job_worker(
        session_factory=session_factory,
        configuration=configuration(),
        handlers=handlers,
        shutdown_event=shutdown,  # type: ignore[arg-type]
        execute_one=execute_one,
    )

    assert captured["session_factory"] is (session_factory)
    assert captured["worker_id"] == "worker-test-01"
    assert captured["lease_duration"] == (timedelta(minutes=5))
    assert captured["heartbeat_interval"] == (timedelta(minutes=1))
    assert captured["handlers"] is handlers


@pytest.mark.parametrize(
    (
        "field",
        "value",
        "message",
    ),
    [
        (
            "worker_id",
            " ",
            "worker_id cannot be empty",
        ),
        (
            "lease_duration",
            timedelta(0),
            "lease_duration must be positive",
        ),
        (
            "heartbeat_interval",
            timedelta(0),
            "heartbeat_interval must be positive",
        ),
        (
            "heartbeat_interval",
            timedelta(minutes=5),
            "must be shorter",
        ),
        (
            "heartbeat_interval",
            timedelta(minutes=6),
            "must be shorter",
        ),
        (
            "poll_seconds",
            0,
            "poll_seconds must be positive",
        ),
        (
            "database_backoff_seconds",
            0,
            "database_backoff_seconds must be positive",
        ),
    ],
)
def test_runner_configuration_rejects_invalid_values(
    field: str,
    value: object,
    message: str,
) -> None:
    values = {
        "worker_id": "worker-test-01",
        "lease_duration": timedelta(minutes=5),
        "heartbeat_interval": timedelta(minutes=1),
        "poll_seconds": 2,
        "database_backoff_seconds": 11,
    }
    values[field] = value

    with pytest.raises(
        ValueError,
        match=message,
    ):
        BackgroundJobRunnerConfiguration(
            **values,  # type: ignore[arg-type]
        )
