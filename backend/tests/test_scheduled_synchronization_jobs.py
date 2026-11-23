from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    InstallationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
)
from app.db.session import engine
from app.services.scheduled_synchronization_jobs import (
    SCHEDULED_SYNC_MAXIMUM_ATTEMPTS,
    SCHEDULED_SYNC_PRIORITY,
    ScheduledSynchronizationTarget,
    enqueue_scheduled_synchronization,
    enqueue_scheduled_synchronization_batch,
    list_scheduled_synchronization_targets,
    schedule_window_key,
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


def scheduled_for() -> datetime:
    return datetime(
        2026,
        11,
        25,
        12,
        0,
        tzinfo=UTC,
    )


def test_active_workspace_and_season_are_eligible(
    database_session: Session,
) -> None:
    targets = list_scheduled_synchronization_targets(database_session)

    assert targets == (
        ScheduledSynchronizationTarget(
            workspace_id=1,
            season_id=1,
        ),
    )


def test_inactive_workspace_is_not_eligible(
    database_session: Session,
) -> None:
    workspace = database_session.get(
        Installation,
        1,
    )

    assert workspace is not None

    workspace.status = InstallationStatus.SETUP_IN_PROGRESS
    database_session.flush()

    targets = list_scheduled_synchronization_targets(database_session)

    assert targets == ()


def test_deletion_pending_workspace_is_not_eligible(
    database_session: Session,
) -> None:
    from datetime import timedelta

    from app.db.platform_enums import WorkspaceRegistrationStatus

    workspace = database_session.get(
        Installation,
        1,
    )

    assert workspace is not None

    requested_at = datetime.now(UTC)
    workspace.registration_status = WorkspaceRegistrationStatus.DELETION_PENDING
    workspace.deletion_requested_at = requested_at
    workspace.deletion_scheduled_for = requested_at + timedelta(days=30)
    database_session.flush()

    assert list_scheduled_synchronization_targets(database_session) == ()


def test_schedule_window_key_is_stable_in_utc() -> None:
    assert schedule_window_key(scheduled_for()) == "20261125T1200Z"


def test_naive_schedule_window_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        schedule_window_key(
            datetime(
                2026,
                11,
                25,
                12,
                0,
            )
        )


def test_scheduled_sync_creates_secret_free_job(
    database_session: Session,
) -> None:
    accepted = enqueue_scheduled_synchronization(
        database_session,
        target=ScheduledSynchronizationTarget(
            workspace_id=1,
            season_id=1,
        ),
        scheduled_for=scheduled_for(),
    )

    job = database_session.get(
        BackgroundJob,
        accepted.job_id,
    )

    assert job is not None
    assert accepted.existing is False
    assert accepted.status is (BackgroundJobStatus.PENDING)
    assert job.job_type is (BackgroundJobType.INCREMENTAL_SEASON_SYNC)
    assert job.payload_version == 1
    assert job.payload == {
        "season_id": 1,
        "full_sync": False,
        "trigger": "scheduled",
    }
    assert job.idempotency_key == ("scheduled-sync:1:1:20261125T1200Z")
    assert job.priority == (SCHEDULED_SYNC_PRIORITY)
    assert job.maximum_attempts == SCHEDULED_SYNC_MAXIMUM_ATTEMPTS

    payload_names = {str(name).casefold() for name in job.payload}

    assert "credentials" not in payload_names
    assert "encrypted_credentials" not in payload_names
    assert "espn_s2" not in payload_names
    assert "swid" not in payload_names


def test_equivalent_active_scheduled_job_is_reused(
    database_session: Session,
) -> None:
    target = ScheduledSynchronizationTarget(
        workspace_id=1,
        season_id=1,
    )

    first = enqueue_scheduled_synchronization(
        database_session,
        target=target,
        scheduled_for=scheduled_for(),
    )
    second = enqueue_scheduled_synchronization(
        database_session,
        target=target,
        scheduled_for=scheduled_for(),
    )

    assert first.existing is False
    assert second.existing is True
    assert second.job_id == first.job_id


def test_next_schedule_window_uses_new_scope(
    database_session: Session,
) -> None:
    target = ScheduledSynchronizationTarget(
        workspace_id=1,
        season_id=1,
    )

    first = enqueue_scheduled_synchronization(
        database_session,
        target=target,
        scheduled_for=scheduled_for(),
    )
    second = enqueue_scheduled_synchronization(
        database_session,
        target=target,
        scheduled_for=datetime(
            2026,
            12,
            2,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    assert second.existing is False
    assert second.job_id != first.job_id


def test_batch_enqueues_all_eligible_targets(
    database_session: Session,
) -> None:
    batch = enqueue_scheduled_synchronization_batch(
        database_session,
        scheduled_for=scheduled_for(),
    )

    assert batch.scheduled_for == scheduled_for()
    assert len(batch.jobs) == 1
    assert batch.created_count == 1
    assert batch.existing_count == 0

    repeated = enqueue_scheduled_synchronization_batch(
        database_session,
        scheduled_for=scheduled_for(),
    )

    assert repeated.created_count == 0
    assert repeated.existing_count == 1


def test_scheduled_priority_is_below_administrator() -> None:
    from app.services.admin_synchronization_jobs import (
        ADMIN_SYNC_PRIORITY,
    )

    assert SCHEDULED_SYNC_PRIORITY < ADMIN_SYNC_PRIORITY


def test_batch_leaves_commit_control_with_caller(
    database_session: Session,
) -> None:
    enqueue_scheduled_synchronization_batch(
        database_session,
        scheduled_for=scheduled_for(),
    )

    assert database_session.in_transaction()


def test_multi_workspace_batch_creates_isolated_jobs(
    database_session: Session,
) -> None:
    from app.repositories.background_job_repository import (
        BackgroundJobRepository,
    )
    from tests.support.multi_workspace_espn import (
        create_multi_workspace_espn_fixture,
    )

    fixture = create_multi_workspace_espn_fixture(database_session)

    targets = list_scheduled_synchronization_targets(database_session)

    assert targets == (
        ScheduledSynchronizationTarget(
            workspace_id=fixture.first.workspace_id,
            season_id=fixture.first.season_id,
        ),
        ScheduledSynchronizationTarget(
            workspace_id=fixture.second.workspace_id,
            season_id=fixture.second.season_id,
        ),
    )

    batch = enqueue_scheduled_synchronization_batch(
        database_session,
        scheduled_for=scheduled_for(),
    )

    assert batch.created_count == 2
    assert batch.existing_count == 0
    assert tuple(
        (
            job.workspace_id,
            job.season_id,
        )
        for job in batch.jobs
    ) == (
        (
            fixture.first.workspace_id,
            fixture.first.season_id,
        ),
        (
            fixture.second.workspace_id,
            fixture.second.season_id,
        ),
    )

    first_job = database_session.get(
        BackgroundJob,
        batch.jobs[0].job_id,
    )
    second_job = database_session.get(
        BackgroundJob,
        batch.jobs[1].job_id,
    )

    assert first_job is not None
    assert second_job is not None
    assert first_job.id != second_job.id
    assert first_job.workspace_id == (fixture.first.workspace_id)
    assert second_job.workspace_id == (fixture.second.workspace_id)
    assert first_job.payload == {
        "season_id": fixture.first.season_id,
        "full_sync": False,
        "trigger": "scheduled",
    }
    assert second_job.payload == {
        "season_id": fixture.second.season_id,
        "full_sync": False,
        "trigger": "scheduled",
    }
    assert first_job.idempotency_key == (
        f"scheduled-sync:{fixture.first.workspace_id}:{fixture.first.season_id}:20261125T1200Z"
    )
    assert second_job.idempotency_key == (
        f"scheduled-sync:{fixture.second.workspace_id}:{fixture.second.season_id}:20261125T1200Z"
    )

    prohibited_names = {
        "credentials",
        "encrypted_credentials",
        "provider_credentials",
        "espn_s2",
        "swid",
    }

    for job in (
        first_job,
        second_job,
    ):
        payload_names = {str(name).casefold() for name in job.payload}

        assert payload_names.isdisjoint(prohibited_names)

    repository = BackgroundJobRepository(database_session)

    assert (
        repository.get_for_workspace(
            job_id=first_job.id,
            workspace_id=(fixture.second.workspace_id),
        )
        is None
    )
    assert (
        repository.get_for_workspace(
            job_id=second_job.id,
            workspace_id=(fixture.first.workspace_id),
        )
        is None
    )

    repeated = enqueue_scheduled_synchronization_batch(
        database_session,
        scheduled_for=scheduled_for(),
    )

    assert repeated.created_count == 0
    assert repeated.existing_count == 2
    assert tuple(job.job_id for job in repeated.jobs) == tuple(job.job_id for job in batch.jobs)
