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
        recovery_interval=timedelta(minutes=1),
        recovery_retry_delay=timedelta(seconds=30),
        recovery_batch_size=100,
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
            "recovery_interval",
            timedelta(0),
            "recovery_interval must be positive",
        ),
        (
            "recovery_retry_delay",
            timedelta(seconds=-1),
            "recovery_retry_delay cannot be negative",
        ),
        (
            "recovery_batch_size",
            0,
            "recovery_batch_size must be positive",
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
        "recovery_interval": timedelta(minutes=1),
        "recovery_retry_delay": timedelta(seconds=30),
        "recovery_batch_size": 100,
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


def test_runner_recovers_expired_leases_before_claiming() -> None:
    from app.services.background_job_lease_recovery import (
        BackgroundJobLeaseRecoveryResult,
    )

    shutdown = RecordingEvent(
        stop_after_waits=1,
    )
    order: list[str] = []

    def recover_expired(
        **kwargs,
    ) -> BackgroundJobLeaseRecoveryResult:
        order.append("recover")
        assert kwargs["retry_delay"] == (timedelta(seconds=30))
        assert kwargs["batch_size"] == 100

        return BackgroundJobLeaseRecoveryResult(
            inspected_count=1,
            retry_scheduled_count=1,
            failed_count=0,
        )

    def execute_one(
        **_kwargs,
    ) -> BackgroundJobExecutionResult:
        order.append("execute")
        return idle_result()

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,  # type: ignore[arg-type]
        execute_one=execute_one,
        recover_expired=recover_expired,
        monotonic_time=lambda: 0.0,
    )

    assert order == [
        "recover",
        "execute",
    ]


def test_runner_scans_recovery_periodically() -> None:
    from app.services.background_job_lease_recovery import (
        BackgroundJobLeaseRecoveryResult,
    )

    shutdown = RecordingEvent(
        stop_after_waits=1,
    )
    recovery_calls = 0
    execution_calls = 0
    monotonic_values = iter(
        [
            0.0,
            30.0,
            60.0,
        ]
    )

    def recover_expired(
        **_kwargs,
    ) -> BackgroundJobLeaseRecoveryResult:
        nonlocal recovery_calls
        recovery_calls += 1

        return BackgroundJobLeaseRecoveryResult(
            inspected_count=0,
            retry_scheduled_count=0,
            failed_count=0,
        )

    def execute_one(
        **_kwargs,
    ) -> BackgroundJobExecutionResult:
        nonlocal execution_calls
        execution_calls += 1

        if execution_calls < 3:
            return completed_result(
                job_id=execution_calls,
            )

        return idle_result()

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,  # type: ignore[arg-type]
        execute_one=execute_one,
        recover_expired=recover_expired,
        monotonic_time=lambda: next(monotonic_values),
    )

    assert execution_calls == 3
    assert recovery_calls == 2


def test_recovery_failure_prevents_job_claim_and_uses_backoff() -> None:
    shutdown = RecordingEvent(
        stop_after_waits=1,
    )
    execute_one = MagicMock()

    def recover_expired(
        **_kwargs,
    ):
        raise OperationalError(
            "SELECT background_jobs",
            {},
            RuntimeError("database unavailable"),
        )

    run_background_job_worker(
        session_factory=MagicMock(),
        configuration=configuration(),
        handlers={},
        shutdown_event=shutdown,  # type: ignore[arg-type]
        execute_one=execute_one,
        recover_expired=recover_expired,
        monotonic_time=lambda: 0.0,
    )

    execute_one.assert_not_called()
    assert shutdown.wait_calls == [11]
