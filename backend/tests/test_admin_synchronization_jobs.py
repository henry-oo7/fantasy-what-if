from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.db.platform_models import BackgroundJob
from app.db.session import engine
from app.services.admin_synchronization_jobs import (
    ADMIN_SYNC_PRIORITY,
    enqueue_administrator_synchronization,
)
from app.services.season_sync_jobs import SEASON_SYNC_MAXIMUM_ATTEMPTS


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


def test_incremental_admin_sync_creates_pending_season_job(
    database_session: Session,
) -> None:
    accepted = enqueue_administrator_synchronization(
        database_session,
        workspace_id=1,
        full_sync=False,
    )

    job = database_session.get(
        BackgroundJob,
        accepted.job_id,
    )

    assert job is not None
    assert accepted.existing is False
    assert accepted.status is BackgroundJobStatus.PENDING
    assert job.workspace_id == 1
    assert job.job_type is BackgroundJobType.LEAGUE_SEASON_SYNC
    assert job.status is BackgroundJobStatus.PENDING
    assert job.payload_version == 1
    assert job.payload == {"version": 1, "season_id": 1}
    assert job.idempotency_key == "league-season-sync:1"
    assert job.priority == ADMIN_SYNC_PRIORITY == 100
    assert job.maximum_attempts == SEASON_SYNC_MAXIMUM_ATTEMPTS == 5


def test_full_and_incremental_admin_sync_share_one_job(
    database_session: Session,
) -> None:
    incremental = enqueue_administrator_synchronization(
        database_session,
        workspace_id=1,
        full_sync=False,
    )
    full = enqueue_administrator_synchronization(
        database_session,
        workspace_id=1,
        full_sync=True,
    )

    assert incremental.existing is False
    assert full.existing is True
    assert full.job_id == incremental.job_id

    job = database_session.get(BackgroundJob, full.job_id)

    assert job is not None
    assert job.job_type is BackgroundJobType.LEAGUE_SEASON_SYNC
    assert job.payload == {"version": 1, "season_id": 1}
    assert job.idempotency_key == "league-season-sync:1"


def test_equivalent_active_job_is_reused(
    database_session: Session,
) -> None:
    first = enqueue_administrator_synchronization(
        database_session,
        workspace_id=1,
        full_sync=False,
    )
    second = enqueue_administrator_synchronization(
        database_session,
        workspace_id=1,
        full_sync=False,
    )

    assert first.existing is False
    assert second.existing is True
    assert second.job_id == first.job_id
    assert second.status is (BackgroundJobStatus.PENDING)


@pytest.mark.parametrize(
    "terminal_status",
    [
        BackgroundJobStatus.SUCCEEDED,
        BackgroundJobStatus.FAILED,
        BackgroundJobStatus.CANCELED,
    ],
)
def test_terminal_job_allows_new_request(
    database_session: Session,
    terminal_status: BackgroundJobStatus,
) -> None:
    first = enqueue_administrator_synchronization(
        database_session,
        workspace_id=1,
        full_sync=False,
    )
    job = database_session.get(
        BackgroundJob,
        first.job_id,
    )

    assert job is not None

    job.status = terminal_status

    if terminal_status is BackgroundJobStatus.SUCCEEDED:
        from datetime import UTC, datetime

        job.completed_at = datetime.now(UTC)
    elif terminal_status is BackgroundJobStatus.FAILED:
        from datetime import UTC, datetime

        job.failed_at = datetime.now(UTC)
    else:
        from datetime import UTC, datetime

        job.canceled_at = datetime.now(UTC)

    database_session.flush()

    second = enqueue_administrator_synchronization(
        database_session,
        workspace_id=1,
        full_sync=False,
    )

    assert second.existing is False
    assert second.job_id != first.job_id


def test_enqueue_does_not_commit_callers_transaction(
    database_session: Session,
) -> None:
    enqueue_administrator_synchronization(
        database_session,
        workspace_id=1,
        full_sync=False,
    )

    assert database_session.in_transaction()


def test_unknown_workspace_is_rejected_without_job(
    database_session: Session,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="workspace is not active",
    ):
        enqueue_administrator_synchronization(
            database_session,
            workspace_id=999_999,
            full_sync=False,
        )

    jobs = database_session.query(BackgroundJob).filter(BackgroundJob.workspace_id == 999_999).all()

    assert jobs == []
