from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.db.platform_models import BackgroundJob
from app.db.session import engine
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)


@pytest.fixture
def database_session() -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()

        if transaction.is_active:
            transaction.connection.close()


def unique_key(
    prefix: str,
) -> str:
    return f"{prefix}:{uuid4().hex}"


def test_enqueue_creates_pending_workspace_job(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    timestamp = datetime(
        2026,
        11,
        18,
        22,
        0,
        tzinfo=UTC,
    )

    result = repository.enqueue(
        workspace_id=1,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        payload={
            "season_id": 1,
            "trigger": "administrator",
        },
        idempotency_key=unique_key("incremental-sync"),
        priority=50,
        available_at=timestamp,
    )

    assert result.existing is False
    assert result.job.id is not None
    assert result.job.workspace_id == 1
    assert result.job.status is (BackgroundJobStatus.PENDING)
    assert result.job.priority == 50
    assert result.job.payload == {
        "season_id": 1,
        "trigger": "administrator",
    }
    assert result.job.attempt_count == 0
    assert result.job.maximum_attempts == 5
    assert result.job.available_at == timestamp
    assert database_session.in_transaction()


def test_idempotent_enqueue_returns_existing_active_job(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    key = unique_key("initial-sync")

    first = repository.enqueue(
        workspace_id=1,
        job_type=(BackgroundJobType.INITIAL_WORKSPACE_SYNC),
        payload={
            "season_id": 1,
        },
        idempotency_key=key,
    )
    second = repository.enqueue(
        workspace_id=1,
        job_type=(BackgroundJobType.INITIAL_WORKSPACE_SYNC),
        payload={
            "season_id": 999,
        },
        idempotency_key=f"  {key}  ",
    )

    assert first.existing is False
    assert second.existing is True
    assert second.job.id == first.job.id
    assert second.job.payload == {
        "season_id": 1,
    }


def test_terminal_job_allows_new_equivalent_job(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    key = unique_key("full-sync")
    timestamp = datetime.now(UTC)

    first = repository.enqueue(
        workspace_id=1,
        job_type=BackgroundJobType.FULL_SEASON_SYNC,
        payload={
            "season_id": 1,
        },
        idempotency_key=key,
    )

    first.job.status = BackgroundJobStatus.SUCCEEDED
    first.job.completed_at = timestamp
    database_session.flush()

    second = repository.enqueue(
        workspace_id=1,
        job_type=BackgroundJobType.FULL_SEASON_SYNC,
        payload={
            "season_id": 1,
        },
        idempotency_key=key,
    )

    assert second.existing is False
    assert second.job.id != first.job.id


def test_claim_uses_priority_and_availability_order(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    now = datetime(
        2026,
        11,
        18,
        22,
        0,
        tzinfo=UTC,
    )

    low_priority = repository.enqueue(
        workspace_id=1,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        payload={
            "season_id": 1,
        },
        idempotency_key=unique_key("low"),
        priority=10,
        available_at=now,
    ).job
    high_priority = repository.enqueue(
        workspace_id=1,
        job_type=(BackgroundJobType.INITIAL_WORKSPACE_SYNC),
        payload={
            "season_id": 1,
        },
        idempotency_key=unique_key("high"),
        priority=100,
        available_at=now,
    ).job
    repository.enqueue(
        workspace_id=1,
        job_type=BackgroundJobType.FULL_SEASON_SYNC,
        payload={
            "season_id": 1,
        },
        idempotency_key=unique_key("future"),
        priority=1_000,
        available_at=now + timedelta(hours=1),
    )

    claimed = repository.claim_next_available(
        worker_id=" worker-01 ",
        lease_duration=timedelta(minutes=5),
        now=now,
    )

    assert claimed is not None
    assert claimed.id == high_priority.id
    assert claimed.id != low_priority.id
    assert claimed.status is (BackgroundJobStatus.RUNNING)
    assert claimed.claimed_by == "worker-01"
    assert claimed.claimed_at == now
    assert claimed.started_at == now
    assert claimed.lease_expires_at == (now + timedelta(minutes=5))
    assert claimed.attempt_count == 1


def test_claim_returns_none_without_available_jobs(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    now = datetime.now(UTC)

    repository.enqueue(
        workspace_id=1,
        job_type=BackgroundJobType.FULL_SEASON_SYNC,
        payload={
            "season_id": 1,
        },
        idempotency_key=unique_key("future-only"),
        available_at=now + timedelta(hours=1),
    )

    claimed = repository.claim_next_available(
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        now=now,
    )

    assert claimed is None


def test_running_and_terminal_jobs_are_not_reclaimed(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    timestamp = datetime.now(UTC)

    running = BackgroundJob(
        workspace_id=1,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        status=BackgroundJobStatus.RUNNING,
        payload_version=1,
        payload={
            "season_id": 1,
        },
        attempt_count=1,
        maximum_attempts=5,
        available_at=timestamp,
        claimed_at=timestamp,
        lease_expires_at=(timestamp + timedelta(minutes=5)),
        claimed_by="existing-worker",
        started_at=timestamp,
    )
    succeeded = BackgroundJob(
        workspace_id=1,
        job_type=BackgroundJobType.FULL_SEASON_SYNC,
        status=BackgroundJobStatus.SUCCEEDED,
        payload_version=1,
        payload={
            "season_id": 1,
        },
        attempt_count=1,
        maximum_attempts=5,
        available_at=timestamp,
        completed_at=timestamp,
    )
    database_session.add_all(
        [
            running,
            succeeded,
        ]
    )
    database_session.flush()

    claimed = repository.claim_next_available(
        worker_id="worker-02",
        lease_duration=timedelta(minutes=5),
        now=timestamp,
    )

    assert claimed is None


def test_get_for_workspace_hides_other_workspace_job(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)

    job = repository.enqueue(
        workspace_id=1,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        payload={
            "season_id": 1,
        },
        idempotency_key=unique_key("workspace-read"),
    ).job

    assert (
        repository.get_for_workspace(
            job_id=job.id,
            workspace_id=1,
        )
        is job
    )
    assert (
        repository.get_for_workspace(
            job_id=job.id,
            workspace_id=999_999,
        )
        is None
    )


def test_claim_statement_uses_skip_locked(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)

    repository.enqueue(
        workspace_id=1,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        payload={
            "season_id": 1,
        },
        idempotency_key=unique_key("skip-locked"),
    )

    statements: list[str] = []

    from sqlalchemy import event

    def capture_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    event.listen(
        database_session.bind,
        "before_cursor_execute",
        capture_statement,
    )

    try:
        repository.claim_next_available(
            worker_id="worker-01",
            lease_duration=timedelta(minutes=5),
        )
    finally:
        event.remove(
            database_session.bind,
            "before_cursor_execute",
            capture_statement,
        )

    normalized_statements = " ".join(statement.upper() for statement in statements)

    assert "FOR UPDATE SKIP LOCKED" in normalized_statements


def test_invalid_enqueue_and_claim_inputs_are_rejected(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)

    with pytest.raises(
        ValueError,
        match="workspace_id must be positive",
    ):
        repository.enqueue(
            workspace_id=0,
            job_type=(BackgroundJobType.FULL_SEASON_SYNC),
            payload={},
        )

    with pytest.raises(
        ValueError,
        match="idempotency_key cannot be empty",
    ):
        repository.enqueue(
            workspace_id=1,
            job_type=(BackgroundJobType.FULL_SEASON_SYNC),
            payload={},
            idempotency_key="   ",
        )

    with pytest.raises(
        ValueError,
        match="worker_id cannot be empty",
    ):
        repository.claim_next_available(
            worker_id=" ",
            lease_duration=timedelta(minutes=5),
        )

    with pytest.raises(
        ValueError,
        match="lease_duration must be positive",
    ):
        repository.claim_next_available(
            worker_id="worker-01",
            lease_duration=timedelta(0),
        )


def test_attempt_exhausted_job_is_not_claimed(
    database_session: Session,
) -> None:
    timestamp = datetime.now(UTC)

    exhausted = BackgroundJob(
        workspace_id=1,
        job_type=BackgroundJobType.FULL_SEASON_SYNC,
        status=BackgroundJobStatus.PENDING,
        payload_version=1,
        payload={
            "season_id": 1,
        },
        attempt_count=5,
        maximum_attempts=5,
        available_at=timestamp,
    )
    database_session.add(exhausted)
    database_session.flush()

    claimed = BackgroundJobRepository(database_session).claim_next_available(
        worker_id="worker-01",
        lease_duration=timedelta(minutes=5),
        now=timestamp,
    )

    assert claimed is None

    stored = database_session.scalar(select(BackgroundJob).where(BackgroundJob.id == exhausted.id))

    assert stored is not None
    assert stored.status is BackgroundJobStatus.PENDING


def create_running_job(
    session: Session,
    *,
    attempt_count: int = 1,
    maximum_attempts: int = 5,
) -> BackgroundJob:
    timestamp = datetime(
        2026,
        11,
        18,
        22,
        0,
        tzinfo=UTC,
    )
    job = BackgroundJob(
        workspace_id=1,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        status=BackgroundJobStatus.RUNNING,
        payload_version=1,
        payload={
            "season_id": 1,
        },
        attempt_count=attempt_count,
        maximum_attempts=maximum_attempts,
        available_at=timestamp,
        claimed_at=timestamp,
        lease_expires_at=(timestamp + timedelta(minutes=5)),
        claimed_by="worker-01",
        started_at=timestamp,
    )
    session.add(job)
    session.flush()

    return job


def test_running_worker_can_renew_lease_and_progress(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    job = create_running_job(database_session)
    timestamp = datetime(
        2026,
        11,
        18,
        22,
        2,
        tzinfo=UTC,
    )

    repository.renew_lease(
        job,
        worker_id="worker-01",
        lease_duration=timedelta(minutes=10),
        now=timestamp,
    )
    repository.update_progress(
        job,
        worker_id="worker-01",
        progress={
            "stage": "fetching_matchups",
            "completed_units": 6,
            "total_units": 12,
        },
    )

    assert job.lease_expires_at == (timestamp + timedelta(minutes=10))
    assert job.progress == {
        "stage": "fetching_matchups",
        "completed_units": 6,
        "total_units": 12,
    }


def test_other_worker_cannot_mutate_running_job(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    job = create_running_job(database_session)

    with pytest.raises(
        RuntimeError,
        match="another worker",
    ):
        repository.update_progress(
            job,
            worker_id="worker-02",
            progress={
                "stage": "unauthorized",
            },
        )


def test_running_job_can_succeed(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    job = create_running_job(database_session)
    timestamp = datetime(
        2026,
        11,
        18,
        22,
        5,
        tzinfo=UTC,
    )

    repository.mark_succeeded(
        job,
        worker_id="worker-01",
        now=timestamp,
    )

    assert job.status is BackgroundJobStatus.SUCCEEDED
    assert job.completed_at == timestamp
    assert job.lease_expires_at is None
    assert job.safe_error_code is None
    assert job.safe_error_message is None


def test_retryable_job_can_be_rescheduled(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    job = create_running_job(database_session)
    available_at = datetime(
        2026,
        11,
        18,
        22,
        10,
        tzinfo=UTC,
    )

    repository.schedule_retry(
        job,
        worker_id="worker-01",
        available_at=available_at,
        safe_error_code="provider_timeout",
        safe_error_message=("The provider request timed out."),
    )

    assert job.status is (BackgroundJobStatus.RETRY_SCHEDULED)
    assert job.available_at == available_at
    assert job.safe_error_code == "provider_timeout"
    assert job.safe_error_message == ("The provider request timed out.")
    assert job.retryable is True
    assert job.claimed_by is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None
    assert job.started_at is None


def test_exhausted_job_cannot_be_rescheduled(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    job = create_running_job(
        database_session,
        attempt_count=5,
        maximum_attempts=5,
    )

    with pytest.raises(
        RuntimeError,
        match="exhausted",
    ):
        repository.schedule_retry(
            job,
            worker_id="worker-01",
            available_at=datetime.now(UTC),
            safe_error_code="provider_timeout",
            safe_error_message=("The provider request timed out."),
        )


def test_running_job_can_fail_terminally(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    job = create_running_job(database_session)
    timestamp = datetime(
        2026,
        11,
        18,
        22,
        5,
        tzinfo=UTC,
    )

    repository.mark_failed(
        job,
        worker_id="worker-01",
        safe_error_code="invalid_credentials",
        safe_error_message=("The provider credentials are invalid."),
        retryable=False,
        now=timestamp,
    )

    assert job.status is BackgroundJobStatus.FAILED
    assert job.failed_at == timestamp
    assert job.safe_error_code == "invalid_credentials"
    assert job.safe_error_message == ("The provider credentials are invalid.")
    assert job.retryable is False
    assert job.lease_expires_at is None


@pytest.mark.parametrize(
    "status",
    [
        BackgroundJobStatus.PENDING,
        BackgroundJobStatus.RETRY_SCHEDULED,
    ],
)
def test_unclaimed_job_can_be_canceled(
    database_session: Session,
    status: BackgroundJobStatus,
) -> None:
    timestamp = datetime.now(UTC)
    job = BackgroundJob(
        workspace_id=1,
        job_type=BackgroundJobType.FULL_SEASON_SYNC,
        status=status,
        payload_version=1,
        payload={
            "season_id": 1,
        },
        attempt_count=0,
        maximum_attempts=5,
        available_at=timestamp,
    )
    database_session.add(job)
    database_session.flush()

    BackgroundJobRepository(database_session).cancel_before_execution(
        job,
        now=timestamp,
    )

    assert job.status is BackgroundJobStatus.CANCELED
    assert job.canceled_at == timestamp


def test_running_job_cannot_use_pre_execution_cancel(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    job = create_running_job(database_session)

    with pytest.raises(
        RuntimeError,
        match="pending or retry-scheduled",
    ):
        repository.cancel_before_execution(job)


def test_expired_running_jobs_are_detected_in_order(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    now = datetime(
        2026,
        11,
        18,
        23,
        0,
        tzinfo=UTC,
    )

    oldest = create_running_job(database_session)
    oldest.lease_expires_at = now - timedelta(minutes=10)

    newer = create_running_job(database_session)
    newer.lease_expires_at = now - timedelta(minutes=2)

    current = create_running_job(database_session)
    current.lease_expires_at = now + timedelta(minutes=5)

    database_session.flush()

    expired = repository.list_expired_running_jobs(
        now=now,
    )

    assert tuple(job.id for job in expired) == (
        oldest.id,
        newer.id,
    )
    assert current not in expired

    assert oldest.status is (BackgroundJobStatus.RUNNING)
    assert newer.status is (BackgroundJobStatus.RUNNING)


def test_expired_lease_detection_respects_limit(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    now = datetime.now(UTC)

    first = create_running_job(database_session)
    first.lease_expires_at = now - timedelta(minutes=3)

    second = create_running_job(database_session)
    second.lease_expires_at = now - timedelta(minutes=2)

    database_session.flush()

    expired = repository.list_expired_running_jobs(
        now=now,
        limit=1,
    )

    assert tuple(job.id for job in expired) == (first.id,)


def test_expired_lease_detection_rejects_invalid_limit(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)

    with pytest.raises(
        ValueError,
        match="limit must be positive",
    ):
        repository.list_expired_running_jobs(
            limit=0,
        )


def test_expired_lease_query_uses_skip_locked(
    database_session: Session,
) -> None:
    now = datetime.now(UTC)
    job = create_running_job(database_session)
    job.lease_expires_at = now - timedelta(minutes=1)
    database_session.flush()

    statements: list[str] = []

    from sqlalchemy import event

    def capture_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    event.listen(
        database_session.bind,
        "before_cursor_execute",
        capture_statement,
    )

    try:
        BackgroundJobRepository(database_session).list_expired_running_jobs(
            now=now,
        )
    finally:
        event.remove(
            database_session.bind,
            "before_cursor_execute",
            capture_statement,
        )

    normalized = " ".join(statement.upper() for statement in statements)

    assert "FOR UPDATE SKIP LOCKED" in normalized


def test_expired_lease_is_scheduled_for_retry(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    now = datetime(
        2026,
        11,
        19,
        20,
        0,
        tzinfo=UTC,
    )
    retry_at = now + timedelta(seconds=30)
    job = create_running_job(
        database_session,
        attempt_count=1,
        maximum_attempts=5,
    )
    job.lease_expires_at = now - timedelta(seconds=1)
    database_session.flush()

    status = repository.recover_expired_lease(
        job,
        retry_at=retry_at,
        now=now,
    )

    assert status is (BackgroundJobStatus.RETRY_SCHEDULED)
    assert job.status is (BackgroundJobStatus.RETRY_SCHEDULED)
    assert job.available_at == retry_at
    assert job.safe_error_code == ("worker_lease_expired")
    assert job.safe_error_message == ("The background worker stopped before the job completed.")
    assert job.retryable is True
    assert job.claimed_by is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None
    assert job.started_at is None
    assert job.failed_at is None


def test_exhausted_expired_lease_fails_terminally(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    now = datetime(
        2026,
        11,
        19,
        20,
        0,
        tzinfo=UTC,
    )
    job = create_running_job(
        database_session,
        attempt_count=5,
        maximum_attempts=5,
    )
    job.lease_expires_at = now - timedelta(seconds=1)
    database_session.flush()

    status = repository.recover_expired_lease(
        job,
        retry_at=now + timedelta(seconds=30),
        now=now,
    )

    assert status is BackgroundJobStatus.FAILED
    assert job.status is BackgroundJobStatus.FAILED
    assert job.failed_at == now
    assert job.safe_error_code == ("worker_lease_expired")
    assert job.retryable is False
    assert job.claimed_by is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None
    assert job.started_at is None


def test_unexpired_lease_cannot_be_recovered(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    now = datetime.now(UTC)
    job = create_running_job(database_session)
    job.lease_expires_at = now + timedelta(minutes=1)
    database_session.flush()

    with pytest.raises(
        RuntimeError,
        match="has not expired",
    ):
        repository.recover_expired_lease(
            job,
            retry_at=now + timedelta(seconds=30),
            now=now,
        )
