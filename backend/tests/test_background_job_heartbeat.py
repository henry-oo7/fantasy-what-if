from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_enums import (
    BackgroundJobType,
)
from app.db.platform_models import BackgroundJob
from app.db.session import engine
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from app.services.background_job_heartbeat import (
    BackgroundJobHeartbeatError,
    BackgroundJobLeaseHeartbeat,
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


def create_running_job(
    factory: sessionmaker[Session],
    *,
    worker_id: str = "worker-01",
) -> int:
    with factory() as session:
        result = BackgroundJobRepository(session).enqueue(
            workspace_id=1,
            job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
            payload={
                "season_id": 1,
            },
            idempotency_key=(f"heartbeat:{uuid4().hex}"),
        )

        job = BackgroundJobRepository(session).claim_next_available(
            worker_id=worker_id,
            lease_duration=(timedelta(minutes=5)),
        )

        assert job is not None
        assert job.id == result.job.id

        job_id = job.id
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


def test_renew_once_extends_running_job_lease(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = create_running_job(isolated_session_factory)
    timestamp = datetime(
        2026,
        11,
        19,
        18,
        0,
        tzinfo=UTC,
    )

    heartbeat = BackgroundJobLeaseHeartbeat(
        session_factory=isolated_session_factory,
        job_id=job_id,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=1),
        now=lambda: timestamp,
    )

    heartbeat.renew_once()

    job = load_job(
        isolated_session_factory,
        job_id,
    )

    assert job.lease_expires_at == (timestamp + timedelta(minutes=5))


def test_renew_once_rejects_worker_ownership_loss(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = create_running_job(
        isolated_session_factory,
        worker_id="worker-01",
    )

    heartbeat = BackgroundJobLeaseHeartbeat(
        session_factory=isolated_session_factory,
        job_id=job_id,
        worker_id="worker-02",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=1),
    )

    with pytest.raises(
        RuntimeError,
        match="another worker",
    ):
        heartbeat.renew_once()


def test_renew_once_rejects_terminal_job(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = create_running_job(isolated_session_factory)

    with isolated_session_factory() as session:
        job = session.get(
            BackgroundJob,
            job_id,
        )

        assert job is not None

        BackgroundJobRepository(session).mark_succeeded(
            job,
            worker_id="worker-01",
        )
        session.commit()

    heartbeat = BackgroundJobLeaseHeartbeat(
        session_factory=isolated_session_factory,
        job_id=job_id,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=1),
    )

    with pytest.raises(
        RuntimeError,
        match="not running",
    ):
        heartbeat.renew_once()


def test_missing_job_is_reported_safely(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    heartbeat = BackgroundJobLeaseHeartbeat(
        session_factory=isolated_session_factory,
        job_id=999_999,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=1),
    )

    with pytest.raises(
        BackgroundJobHeartbeatError,
        match="no longer exists",
    ):
        heartbeat.renew_once()


@pytest.mark.parametrize(
    (
        "job_id",
        "worker_id",
        "lease_duration",
        "heartbeat_interval",
        "message",
    ),
    [
        (
            0,
            "worker-01",
            timedelta(minutes=5),
            timedelta(minutes=1),
            "job_id must be positive",
        ),
        (
            1,
            " ",
            timedelta(minutes=5),
            timedelta(minutes=1),
            "worker_id cannot be empty",
        ),
        (
            1,
            "worker-01",
            timedelta(0),
            timedelta(minutes=1),
            "lease_duration must be positive",
        ),
        (
            1,
            "worker-01",
            timedelta(minutes=5),
            timedelta(0),
            "heartbeat_interval must be positive",
        ),
        (
            1,
            "worker-01",
            timedelta(minutes=5),
            timedelta(minutes=5),
            "must be shorter",
        ),
        (
            1,
            "worker-01",
            timedelta(minutes=5),
            timedelta(minutes=6),
            "must be shorter",
        ),
    ],
)
def test_heartbeat_rejects_unsafe_configuration(
    isolated_session_factory: (sessionmaker[Session]),
    job_id: int,
    worker_id: str,
    lease_duration: timedelta,
    heartbeat_interval: timedelta,
    message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=message,
    ):
        BackgroundJobLeaseHeartbeat(
            session_factory=(isolated_session_factory),
            job_id=job_id,
            worker_id=worker_id,
            lease_duration=lease_duration,
            heartbeat_interval=heartbeat_interval,
        )


def test_start_cannot_be_called_twice(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = create_running_job(isolated_session_factory)
    heartbeat = BackgroundJobLeaseHeartbeat(
        session_factory=isolated_session_factory,
        job_id=job_id,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=4),
    )

    heartbeat.start()

    try:
        with pytest.raises(
            RuntimeError,
            match="already started",
        ):
            heartbeat.start()
    finally:
        heartbeat.stop()


def test_background_failure_is_reported_to_executor(
    isolated_session_factory: (sessionmaker[Session]),
) -> None:
    job_id = create_running_job(
        isolated_session_factory,
        worker_id="worker-01",
    )
    heartbeat = BackgroundJobLeaseHeartbeat(
        session_factory=isolated_session_factory,
        job_id=job_id,
        worker_id="worker-02",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(milliseconds=1),
    )

    heartbeat.start()

    try:
        assert heartbeat._stop_event.wait(
            timeout=1,
        )

        with pytest.raises(
            BackgroundJobHeartbeatError,
            match="could not be renewed safely",
        ):
            heartbeat.raise_if_failed()
    finally:
        heartbeat.stop()
