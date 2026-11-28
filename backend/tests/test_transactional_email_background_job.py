"""BackgroundJob nullability and payload boundary for email jobs (ADR 0009)."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_enums import BackgroundJobStatus, BackgroundJobType
from app.db.platform_models import BackgroundJob
from app.notifications.delivery_job_contract import (
    build_transactional_email_job_payload,
    transactional_email_job_idempotency_key,
    validate_transactional_email_job_payload,
)
from app.repositories.background_job_repository import BackgroundJobRepository
from app.services.background_job_worker import execute_one_background_job
from tests.support.transactional_email import (
    assert_accepted,
    assert_rejected,
    database_session_fixture,
    make_workspace,
)

EMAIL = BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY


@pytest.fixture
def database_session() -> Iterator[Session]:
    yield from database_session_fixture()


def job(workspace_id: int | None, job_type: BackgroundJobType, payload: dict) -> BackgroundJob:
    return BackgroundJob(workspace_id=workspace_id, job_type=job_type, payload=payload)


GOOD_PAYLOAD = {"version": 1, "delivery_id": 1}


def test_every_non_email_job_type_requires_a_workspace(database_session: Session) -> None:
    workspace = make_workspace(database_session)

    # League-Season sync, access reverification and orphan purge may omit their workspace;
    # their own tests cover them.
    for job_type in BackgroundJobType:
        if job_type in (
            EMAIL,
            BackgroundJobType.LEAGUE_SEASON_SYNC,
            BackgroundJobType.ACCESS_REVERIFICATION,
            BackgroundJobType.ORPHAN_LEAGUE_PURGE,
        ):
            continue

        assert_rejected(database_session, job(None, job_type, {}))
        assert_accepted(database_session, job(workspace.id, job_type, {}))


def test_email_job_allows_null_or_scoped_workspace(database_session: Session) -> None:
    workspace = make_workspace(database_session)

    assert_accepted(database_session, job(None, EMAIL, GOOD_PAYLOAD))
    assert_accepted(database_session, job(workspace.id, EMAIL, GOOD_PAYLOAD))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 1},
        {"delivery_id": 1},
        {"version": 1, "delivery_id": 1, "recipient": "a@b.invalid"},
        {"version": 1, "delivery_id": 1, "event_type": "x"},
        {"version": 1, "delivery_id": "1"},
        {"version": "1", "delivery_id": 1},
        {"version": 1, "delivery_id": None},
        [],
    ],
)
def test_database_rejects_malformed_email_payloads(
    database_session: Session, payload: object
) -> None:
    workspace = make_workspace(database_session)

    assert_rejected(database_session, job(workspace.id, EMAIL, payload))
    assert_rejected(database_session, job(None, EMAIL, payload))


def test_payload_contract_builds_exact_allowlist() -> None:
    payload = build_transactional_email_job_payload(42)

    assert payload == {"version": 1, "delivery_id": 42}
    assert validate_transactional_email_job_payload(payload) == 42
    assert transactional_email_job_idempotency_key(42) == "transactional-email-delivery:42"


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "delivery_id": 1, "token": "t"},
        {"version": 1, "delivery_id": 1, "workspace_slug": "s"},
        {"version": 1, "delivery_id": 1, "user_id": 1},
        {"version": 2, "delivery_id": 1},
        {"version": True, "delivery_id": 1},
        {"version": 1, "delivery_id": 0},
        {"version": 1, "delivery_id": True},
        {"version": 1},
        None,
    ],
)
def test_payload_contract_rejects_everything_else(payload: object) -> None:
    with pytest.raises(ValueError):
        validate_transactional_email_job_payload(payload)


def test_repository_enforces_workspace_and_payload_rules(database_session: Session) -> None:
    repository = BackgroundJobRepository(database_session)
    workspace = make_workspace(database_session)

    with pytest.raises(ValueError, match="omit workspace_id"):
        repository.enqueue(
            workspace_id=None,
            job_type=BackgroundJobType.FULL_SEASON_SYNC,
            payload={"season_id": 1},
        )

    with pytest.raises(ValueError, match="exactly"):
        repository.enqueue(
            workspace_id=workspace.id,
            job_type=EMAIL,
            payload={"version": 1, "delivery_id": 1, "recipient": "a@b.invalid"},
        )

    result = repository.enqueue(workspace_id=None, job_type=EMAIL, payload=GOOD_PAYLOAD)
    assert result.job.workspace_id is None


def test_workspace_keyed_reads_never_match_null_workspace_jobs(database_session: Session) -> None:
    repository = BackgroundJobRepository(database_session)
    workspace = make_workspace(database_session)
    unscoped = repository.enqueue(workspace_id=None, job_type=EMAIL, payload=GOOD_PAYLOAD).job

    assert repository.get_for_workspace(job_id=unscoped.id, workspace_id=workspace.id) is None
    assert repository.cancel_queued_workspace_jobs(workspace_id=workspace.id) == 0
    database_session.refresh(unscoped)
    assert unscoped.status is BackgroundJobStatus.PENDING


def test_workspace_cascade_removes_scoped_email_job_but_not_unscoped(
    database_session: Session,
) -> None:
    repository = BackgroundJobRepository(database_session)
    workspace = make_workspace(database_session)
    scoped = repository.enqueue(workspace_id=workspace.id, job_type=EMAIL, payload=GOOD_PAYLOAD).job
    unscoped = repository.enqueue(workspace_id=None, job_type=EMAIL, payload=GOOD_PAYLOAD).job
    scoped_id, unscoped_id = scoped.id, unscoped.id

    database_session.delete(workspace)
    database_session.flush()
    database_session.expire_all()

    assert database_session.get(BackgroundJob, scoped_id) is None
    assert database_session.get(BackgroundJob, unscoped_id) is not None


def test_email_job_without_handler_fails_terminally_until_worker_slice() -> None:
    from app.db.session import engine

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
        with factory() as session:
            job_id = (
                BackgroundJobRepository(session)
                .enqueue(workspace_id=None, job_type=EMAIL, payload=GOOD_PAYLOAD)
                .job.id
            )
            session.commit()

        result = execute_one_background_job(
            session_factory=factory,
            worker_id="worker-01",
            lease_duration=timedelta(minutes=5),
            handlers={},
        )

        with factory() as session:
            stored = session.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id))

        assert result.failed is True
        assert stored is not None
        assert stored.safe_error_code == "unsupported_job_type"
        assert stored.status is BackgroundJobStatus.FAILED
    finally:
        if transaction.is_active:
            transaction.rollback()

        connection.close()


def test_unique_active_idempotency_still_applies_to_email_jobs(database_session: Session) -> None:
    repository = BackgroundJobRepository(database_session)
    first = repository.enqueue(
        workspace_id=None,
        job_type=EMAIL,
        payload=GOOD_PAYLOAD,
        idempotency_key="transactional-email-delivery:1",
    )
    second = repository.enqueue(
        workspace_id=None,
        job_type=EMAIL,
        payload=GOOD_PAYLOAD,
        idempotency_key="transactional-email-delivery:1",
    )

    assert first.existing is False and second.existing is True
    assert second.job.id == first.job.id
