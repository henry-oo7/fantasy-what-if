from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from random import Random
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
)
from app.db.platform_models import BackgroundJob
from app.db.session import engine
from app.providers.errors import (
    ProviderAuthenticationError,
    ProviderErrorContext,
    ProviderUnavailableError,
)
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from app.services.background_job_heartbeat import (
    BackgroundJobHeartbeatError,
)
from app.services.background_job_worker import (
    BackgroundJobSnapshot,
    calculate_retry_time,
    execute_one_background_job,
)


@pytest.fixture
def isolated_session_factory() -> Iterator[sessionmaker[Session]]:
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield factory
    finally:
        if transaction.is_active:
            transaction.rollback()

        connection.close()


def enqueue_job(
    factory: sessionmaker[Session],
    *,
    maximum_attempts: int = 5,
    available_at: datetime | None = None,
) -> int:
    with factory() as session:
        result = BackgroundJobRepository(session).enqueue(
            workspace_id=1,
            job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
            payload={
                "season_id": 1,
                "trigger": "test",
            },
            idempotency_key=(f"worker-test:{uuid4().hex}"),
            maximum_attempts=maximum_attempts,
            available_at=available_at,
        )
        job_id = result.job.id

        assert job_id is not None

        session.commit()

    return job_id


def load_job(
    factory: sessionmaker[Session],
    job_id: int,
) -> BackgroundJob:
    with factory() as session:
        job = session.get(
            BackgroundJob,
            job_id,
        )

        assert job is not None

        session.expunge(job)

    return job


def test_worker_claims_executes_and_succeeds(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(isolated_session_factory)
    snapshots: list[BackgroundJobSnapshot] = []

    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        handlers={
            (BackgroundJobType.INCREMENTAL_SEASON_SYNC): snapshots.append,
        },
        jitter_ratio=0,
    )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert result.job_id == job_id
    assert result.claimed is True
    assert result.succeeded is True
    assert len(snapshots) == 1
    assert snapshots[0].id == job_id
    assert snapshots[0].workspace_id == 1
    assert snapshots[0].payload == {
        "season_id": 1,
        "trigger": "test",
    }
    assert job.status is (BackgroundJobStatus.SUCCEEDED)
    assert job.attempt_count == 1
    assert job.completed_at is not None


def test_worker_returns_idle_without_available_job(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        handlers={},
    )

    assert result.job_id is None
    assert result.claimed is False
    assert result.succeeded is False
    assert result.retry_scheduled is False
    assert result.failed is False


def test_retryable_provider_error_schedules_retry(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    timestamp = datetime(
        2026,
        11,
        18,
        23,
        0,
        tzinfo=UTC,
    )
    job_id = enqueue_job(
        isolated_session_factory,
        available_at=timestamp,
    )

    def handler(
        _job: BackgroundJobSnapshot,
    ) -> None:
        raise ProviderUnavailableError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="fetch_matchups",
                retryable=True,
            ),
            safe_message=("The provider is temporarily unavailable."),
        )

    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        handlers={
            (BackgroundJobType.INCREMENTAL_SEASON_SYNC): handler,
        },
        now=lambda: timestamp,
        retry_delays=(timedelta(seconds=30),),
        jitter_ratio=0,
    )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert result.retry_scheduled is True
    assert result.failed is False
    assert job.status is (BackgroundJobStatus.RETRY_SCHEDULED)
    assert job.available_at == (timestamp + timedelta(seconds=30))
    assert job.safe_error_code == "unavailable"
    assert job.safe_error_message == ("The provider is temporarily unavailable.")
    assert job.retryable is True
    assert job.claimed_by is None


def test_nonretryable_provider_error_fails_job(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(isolated_session_factory)

    def handler(
        _job: BackgroundJobSnapshot,
    ) -> None:
        raise ProviderAuthenticationError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="fetch_league",
                retryable=False,
            ),
        )

    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        handlers={
            (BackgroundJobType.INCREMENTAL_SEASON_SYNC): handler,
        },
    )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert result.failed is True
    assert result.retry_scheduled is False
    assert job.status is BackgroundJobStatus.FAILED
    assert job.safe_error_code == "authentication"
    assert job.retryable is False
    assert "credentials" in (job.safe_error_message.casefold())


def test_exhausted_retryable_error_fails_terminally(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(
        isolated_session_factory,
        maximum_attempts=1,
    )

    def handler(
        _job: BackgroundJobSnapshot,
    ) -> None:
        raise ProviderUnavailableError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="fetch_matchups",
                retryable=True,
            ),
        )

    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        handlers={
            (BackgroundJobType.INCREMENTAL_SEASON_SYNC): handler,
        },
    )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert result.failed is True
    assert result.retry_scheduled is False
    assert job.status is BackgroundJobStatus.FAILED
    assert job.attempt_count == 1
    assert job.retryable is True


def test_unexpected_exception_uses_safe_failure(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(isolated_session_factory)
    private_value = "private-provider-response"

    def handler(
        _job: BackgroundJobSnapshot,
    ) -> None:
        raise RuntimeError(private_value)

    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        handlers={
            (BackgroundJobType.INCREMENTAL_SEASON_SYNC): handler,
        },
    )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert result.failed is True
    assert job.safe_error_code == "unexpected_job_failure"
    assert private_value not in (job.safe_error_message)
    assert job.retryable is False


def test_missing_handler_fails_without_execution(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(isolated_session_factory)

    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        handlers={},
    )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert result.failed is True
    assert job.safe_error_code == "unsupported_job_type"
    assert job.status is BackgroundJobStatus.FAILED


def test_retry_jitter_stays_within_configured_bounds() -> None:
    timestamp = datetime(
        2026,
        11,
        18,
        23,
        0,
        tzinfo=UTC,
    )

    retry_at = calculate_retry_time(
        attempt_count=1,
        now=timestamp,
        retry_delays=(timedelta(seconds=30),),
        jitter_ratio=0.2,
        random_source=Random(7),
    )

    delay = retry_at - timestamp

    assert timedelta(seconds=24) <= delay
    assert delay <= timedelta(seconds=36)


@pytest.mark.parametrize(
    (
        "attempt_count",
        "expected_delay",
    ),
    [
        (
            1,
            timedelta(seconds=30),
        ),
        (
            2,
            timedelta(minutes=2),
        ),
        (
            3,
            timedelta(minutes=10),
        ),
        (
            4,
            timedelta(minutes=30),
        ),
        (
            5,
            timedelta(minutes=30),
        ),
    ],
)
def test_retry_delay_increases_by_attempt(
    attempt_count: int,
    expected_delay: timedelta,
) -> None:
    timestamp = datetime.now(UTC)

    retry_at = calculate_retry_time(
        attempt_count=attempt_count,
        now=timestamp,
        jitter_ratio=0,
    )

    assert retry_at - timestamp == expected_delay


class FakeHeartbeat:
    def __init__(
        self,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.failure = failure
        self.started = False
        self.stopped = False
        self.raise_checked = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def raise_if_failed(self) -> None:
        self.raise_checked = True

        if self.failure is not None:
            raise self.failure


def test_executor_starts_and_stops_heartbeat(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(isolated_session_factory)
    heartbeat = FakeHeartbeat()
    factory_arguments: dict[str, object] = {}

    def heartbeat_factory(
        **kwargs,
    ) -> FakeHeartbeat:
        factory_arguments.update(kwargs)
        return heartbeat

    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=1),
        heartbeat_factory=heartbeat_factory,
        handlers={
            (BackgroundJobType.INCREMENTAL_SEASON_SYNC): lambda _job: None,
        },
    )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert result.succeeded is True
    assert heartbeat.started is True
    assert heartbeat.stopped is True
    assert heartbeat.raise_checked is True
    assert factory_arguments["job_id"] == job_id
    assert factory_arguments["worker_id"] == "worker-01"
    assert factory_arguments["lease_duration"] == (timedelta(minutes=5))
    assert factory_arguments["heartbeat_interval"] == timedelta(minutes=1)
    assert job.status is (BackgroundJobStatus.SUCCEEDED)


def test_heartbeat_stops_when_handler_fails(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(isolated_session_factory)
    heartbeat = FakeHeartbeat()

    def handler(
        _job: BackgroundJobSnapshot,
    ) -> None:
        raise ProviderAuthenticationError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="fetch_league",
                retryable=False,
            ),
        )

    result = execute_one_background_job(
        session_factory=isolated_session_factory,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=1),
        heartbeat_factory=lambda **_kwargs: heartbeat,
        handlers={
            (BackgroundJobType.INCREMENTAL_SEASON_SYNC): handler,
        },
    )

    assert result.failed is True
    assert heartbeat.started is True
    assert heartbeat.stopped is True
    assert heartbeat.raise_checked is True

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert job.status is BackgroundJobStatus.FAILED


def test_heartbeat_failure_leaves_job_recoverable(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(isolated_session_factory)
    heartbeat = FakeHeartbeat(
        failure=BackgroundJobHeartbeatError("The background job lease could not be renewed safely.")
    )

    with pytest.raises(
        BackgroundJobHeartbeatError,
        match="could not be renewed safely",
    ):
        execute_one_background_job(
            session_factory=(isolated_session_factory),
            worker_id="worker-01",
            lease_duration=timedelta(minutes=5),
            heartbeat_interval=(timedelta(minutes=1)),
            heartbeat_factory=lambda **_kwargs: heartbeat,
            handlers={
                (BackgroundJobType.INCREMENTAL_SEASON_SYNC): lambda _job: None,
            },
        )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert heartbeat.started is True
    assert heartbeat.stopped is True
    assert heartbeat.raise_checked is True
    assert job.status is BackgroundJobStatus.RUNNING
    assert job.completed_at is None
    assert job.failed_at is None
    assert job.claimed_by == "worker-01"


def test_heartbeat_failure_overrides_handler_failure(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = enqueue_job(isolated_session_factory)
    heartbeat = FakeHeartbeat(
        failure=BackgroundJobHeartbeatError("The background job lease could not be renewed safely.")
    )

    def handler(
        _job: BackgroundJobSnapshot,
    ) -> None:
        raise RuntimeError("private provider failure")

    with pytest.raises(
        BackgroundJobHeartbeatError,
        match="could not be renewed safely",
    ):
        execute_one_background_job(
            session_factory=(isolated_session_factory),
            worker_id="worker-01",
            lease_duration=timedelta(minutes=5),
            heartbeat_interval=(timedelta(minutes=1)),
            heartbeat_factory=lambda **_kwargs: heartbeat,
            handlers={
                (BackgroundJobType.INCREMENTAL_SEASON_SYNC): handler,
            },
        )

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert job.status is BackgroundJobStatus.RUNNING
    assert job.safe_error_code is None
    assert job.safe_error_message is None
