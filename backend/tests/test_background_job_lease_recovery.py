from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.db.platform_models import BackgroundJob
from app.db.session import engine
from app.services.background_job_lease_recovery import (
    recover_expired_background_jobs,
)


@pytest.fixture
def database_session():
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
            transaction.rollback()

        connection.close()


def create_expired_job(
    session: Session,
    *,
    now: datetime,
    attempt_count: int,
    maximum_attempts: int,
) -> BackgroundJob:
    job = BackgroundJob(
        workspace_id=1,
        job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        status=BackgroundJobStatus.RUNNING,
        payload_version=1,
        payload={
            "season_id": 1,
            "full_sync": False,
            "trigger": "test",
        },
        attempt_count=attempt_count,
        maximum_attempts=maximum_attempts,
        available_at=now - timedelta(minutes=10),
        claimed_at=now - timedelta(minutes=6),
        lease_expires_at=now - timedelta(minutes=1),
        claimed_by="stopped-worker",
        started_at=now - timedelta(minutes=6),
    )
    session.add(job)
    session.flush()

    return job


def test_recovery_reschedules_and_fails_expired_jobs(
    database_session: Session,
) -> None:
    now = datetime(
        2026,
        11,
        19,
        20,
        0,
        tzinfo=UTC,
    )
    retryable_job = create_expired_job(
        database_session,
        now=now,
        attempt_count=1,
        maximum_attempts=5,
    )
    exhausted_job = create_expired_job(
        database_session,
        now=now,
        attempt_count=5,
        maximum_attempts=5,
    )

    result = recover_expired_background_jobs(
        database_session,
        now=now,
        retry_delay=timedelta(seconds=30),
    )

    assert result.inspected_count == 2
    assert result.recovered_count == 2
    assert result.retry_scheduled_count == 1
    assert result.failed_count == 1

    assert retryable_job.status is (BackgroundJobStatus.RETRY_SCHEDULED)
    assert retryable_job.available_at == (now + timedelta(seconds=30))
    assert exhausted_job.status is (BackgroundJobStatus.FAILED)
    assert exhausted_job.failed_at == now


def test_recovery_ignores_current_lease(
    database_session: Session,
) -> None:
    now = datetime.now(UTC)
    job = create_expired_job(
        database_session,
        now=now,
        attempt_count=1,
        maximum_attempts=5,
    )
    job.lease_expires_at = now + timedelta(minutes=1)
    database_session.flush()

    result = recover_expired_background_jobs(
        database_session,
        now=now,
    )

    assert result.inspected_count == 0
    assert result.recovered_count == 0
    assert job.status is BackgroundJobStatus.RUNNING


def test_recovery_respects_batch_size(
    database_session: Session,
) -> None:
    now = datetime.now(UTC)

    for _ in range(3):
        create_expired_job(
            database_session,
            now=now,
            attempt_count=1,
            maximum_attempts=5,
        )

    result = recover_expired_background_jobs(
        database_session,
        now=now,
        batch_size=2,
    )

    assert result.inspected_count == 2
    assert result.retry_scheduled_count == 2


@pytest.mark.parametrize(
    (
        "retry_delay",
        "batch_size",
        "message",
    ),
    [
        (
            timedelta(seconds=-1),
            100,
            "retry_delay cannot be negative",
        ),
        (
            timedelta(seconds=30),
            0,
            "batch_size must be positive",
        ),
    ],
)
def test_recovery_rejects_invalid_configuration(
    database_session: Session,
    retry_delay: timedelta,
    batch_size: int,
    message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=message,
    ):
        recover_expired_background_jobs(
            database_session,
            retry_delay=retry_delay,
            batch_size=batch_size,
        )


def test_committed_recovery_persists_outcome(
    database_session: Session,
) -> None:
    from sqlalchemy.orm import sessionmaker

    from app.services.background_job_lease_recovery import (
        recover_expired_background_jobs_committed,
    )

    now = datetime(
        2026,
        11,
        19,
        20,
        0,
        tzinfo=UTC,
    )
    job = create_expired_job(
        database_session,
        now=now,
        attempt_count=1,
        maximum_attempts=5,
    )
    job_id = job.id

    assert job_id is not None

    database_session.commit()

    factory = sessionmaker(
        bind=database_session.get_bind(),
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )

    result = recover_expired_background_jobs_committed(
        session_factory=factory,
        now=now,
        retry_delay=timedelta(seconds=30),
    )

    database_session.expire_all()

    stored = database_session.get(
        BackgroundJob,
        job_id,
    )

    assert stored is not None
    assert result.recovered_count == 1
    assert stored.status is (BackgroundJobStatus.RETRY_SCHEDULED)
    assert stored.available_at == (now + timedelta(seconds=30))
    assert stored.claimed_by is None


def test_committed_recovery_commits_empty_scan() -> None:
    from unittest.mock import MagicMock

    from app.services.background_job_lease_recovery import (
        recover_expired_background_jobs_committed,
    )

    session = MagicMock()
    session_context = MagicMock()
    session_context.__enter__.return_value = session
    session_factory = MagicMock(
        return_value=session_context,
    )

    session.scalars.return_value.all.return_value = []

    result = recover_expired_background_jobs_committed(
        session_factory=session_factory,
        now=datetime(
            2026,
            11,
            19,
            20,
            0,
            tzinfo=UTC,
        ),
    )

    assert result.inspected_count == 0
    assert result.recovered_count == 0
    session.commit.assert_called_once()
