from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.db.session import engine
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from app.services.background_job_status import (
    get_background_job_status,
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
            transaction.rollback()

        connection.close()


def create_job(
    session: Session,
) -> int:
    job = (
        BackgroundJobRepository(session)
        .enqueue(
            workspace_id=1,
            job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
            payload={
                "season_id": 1,
                "full_sync": False,
                "trigger": "administrator",
            },
            idempotency_key=(f"status-test:{datetime.now(UTC).timestamp()}"),
            priority=100,
        )
        .job
    )

    assert job.id is not None

    return job.id


def test_workspace_can_read_safe_job_status(
    database_session: Session,
) -> None:
    job_id = create_job(database_session)

    snapshot = get_background_job_status(
        database_session,
        job_id=job_id,
        workspace_id=1,
    )

    assert snapshot is not None
    assert snapshot.job_id == job_id
    assert snapshot.job_type is (BackgroundJobType.INCREMENTAL_SEASON_SYNC)
    assert snapshot.status is (BackgroundJobStatus.PENDING)
    assert snapshot.attempt_count == 0
    assert snapshot.maximum_attempts == 5
    assert snapshot.progress is None
    assert snapshot.started_at is None
    assert snapshot.completed_at is None
    assert snapshot.failed_at is None
    assert snapshot.canceled_at is None
    assert snapshot.safe_error_code is None
    assert snapshot.safe_error_message is None
    assert snapshot.retryable is None


def test_other_workspace_receives_no_job_status(
    database_session: Session,
) -> None:
    job_id = create_job(database_session)

    snapshot = get_background_job_status(
        database_session,
        job_id=job_id,
        workspace_id=999_999,
    )

    assert snapshot is None


def test_unknown_job_returns_none(
    database_session: Session,
) -> None:
    snapshot = get_background_job_status(
        database_session,
        job_id=999_999,
        workspace_id=1,
    )

    assert snapshot is None


def test_progress_is_copied_from_persisted_job(
    database_session: Session,
) -> None:
    job_id = create_job(database_session)
    repository = BackgroundJobRepository(database_session)
    job = repository.get_for_workspace(
        job_id=job_id,
        workspace_id=1,
    )

    assert job is not None

    job.progress = {
        "stage": "fetching_matchups",
        "completed_units": 4,
        "total_units": 12,
    }
    database_session.flush()

    snapshot = get_background_job_status(
        database_session,
        job_id=job_id,
        workspace_id=1,
    )

    assert snapshot is not None
    assert snapshot.progress == {
        "stage": "fetching_matchups",
        "completed_units": 4,
        "total_units": 12,
    }

    snapshot.progress["completed_units"] = 999

    assert job.progress["completed_units"] == 4
