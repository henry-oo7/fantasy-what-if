"""Routine FR-20 deletion-email worker behavior on the test database.

Deliveries are created by the real request and cancellation services; the
transactional-email handler runs with an in-memory fake transport and a
fixed clock, so nothing here reaches a provider or the network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update

from app.db.auth_models import AuthSessionPurpose, User
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
)
from app.db.platform_models import BackgroundJob, TransactionalEmailDelivery
from app.db.session import SessionFactory
from app.notifications.delivery_job_contract import transactional_email_job_idempotency_key
from app.notifications.email_provider import EmailAcceptance, OutgoingEmail
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.session_repository import SessionRepository
from app.services.background_job_worker import (
    BackgroundJobSnapshot,
    execute_one_background_job,
    snapshot_background_job,
)
from app.services.transactional_email_delivery import TransactionalEmailDeliveryHandler
from app.services.transactional_email_intents import queue_workspace_email
from app.services.workspace_deletion import (
    cancel_workspace_deletion,
    request_workspace_deletion,
)
from app.services.workspace_deletion_email import (
    deletion_canceled_key,
    deletion_reminder_key,
    deletion_requested_key,
)
from app.services.workspace_deletion_execution import execute_workspace_deletion
from tests.support.email_delivery import FakeTransport, email_settings, generated_keyring_json
from tests.support.workspace_deletion_graph import (
    ADMIN_PASSWORD,
    DeletionGraph,
    cleanup_graphs,
    completion_deliveries,
    create_deletion_graph,
    deletion_recipient_cipher,
    enqueue_deletion_job,
)

REQUESTED = TransactionalEmailEventType.WORKSPACE_DELETION_REQUESTED
REMINDER = TransactionalEmailEventType.WORKSPACE_DELETION_REMINDER
CANCELED = TransactionalEmailEventType.WORKSPACE_DELETION_CANCELED
VERIFICATION = TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION
PASSWORD_RESET = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET
EMAIL_JOB = BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY
WORKER = "deletion-email-worker-routine"
LEASE = timedelta(minutes=5)
SETTINGS = email_settings(keys=generated_keyring_json())
STATUS = TransactionalEmailDeliveryStatus


# Fixtures and helpers ---------------------------------------------------


@pytest.fixture
def graphs() -> Iterator[Callable[..., DeletionGraph]]:
    created: list[int] = []

    def create(**kwargs) -> DeletionGraph:
        graph = create_deletion_graph(seasons=1, teams=2, weeks=1, players_per_team=1, **kwargs)
        created.append(graph.workspace_id)
        return graph

    yield create

    cleanup_graphs(created)


def accept(message: OutgoingEmail) -> EmailAcceptance:
    return EmailAcceptance(provider_message_id=f"synthetic-{message.idempotency_key}")


def standard_session(graph: DeletionGraph) -> int:
    with SessionFactory() as session:
        user = session.get(User, graph.administrator_id)
        created = SessionRepository(session).create_session(user=user, lifetime_hours=8)
        session.commit()
        return created.session.id


def recovery_session(graph: DeletionGraph, *, now: datetime | None = None) -> int:
    with SessionFactory() as session:
        user = session.get(User, graph.administrator_id)
        created = SessionRepository(session).create_session(
            user=user,
            lifetime=timedelta(minutes=15),
            purpose=AuthSessionPurpose.DELETION_RECOVERY,
            now=now,
        )
        session.commit()
        return created.session.id


def request(graph: DeletionGraph, *, now: datetime | None = None):
    with SessionFactory() as session:
        state = request_workspace_deletion(
            session,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            auth_session_id=standard_session(graph),
            current_password=ADMIN_PASSWORD,
            confirmation_slug=graph.slug,
            now=now,
        )
        session.commit()
        return state


def cancel(graph: DeletionGraph, *, now: datetime | None = None) -> None:
    with SessionFactory() as session:
        cancel_workspace_deletion(
            session,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            auth_session_id=recovery_session(graph, now=now),
            current_password=ADMIN_PASSWORD,
            now=now,
        )
        session.commit()


def deliveries(workspace_id: int) -> list[TransactionalEmailDelivery]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(TransactionalEmailDelivery)
                .where(TransactionalEmailDelivery.workspace_id == workspace_id)
                .order_by(TransactionalEmailDelivery.id)
            )
        )
        for row in rows:
            session.expunge(row)
        return rows


def one(workspace_id: int, event: TransactionalEmailEventType) -> TransactionalEmailDelivery:
    (row,) = [r for r in deliveries(workspace_id) if r.event_type is event]
    return row


def delivery_row(delivery_id: int) -> TransactionalEmailDelivery:
    with SessionFactory() as session:
        row = session.get(TransactionalEmailDelivery, delivery_id)
        assert row is not None
        session.expunge(row)
        return row


def job_for(delivery_id: int) -> BackgroundJob:
    with SessionFactory() as session:
        job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key
                == transactional_email_job_idempotency_key(delivery_id)
            )
        )
        assert job is not None
        session.expunge(job)
        return job


def email_snapshot(delivery_id: int) -> BackgroundJobSnapshot:
    with SessionFactory() as session:
        job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key
                == transactional_email_job_idempotency_key(delivery_id),
                BackgroundJob.status.in_(
                    (BackgroundJobStatus.PENDING, BackgroundJobStatus.RETRY_SCHEDULED)
                ),
            )
        )
        assert job is not None
        return snapshot_background_job(job)


def email_handler(transport: FakeTransport, now: datetime) -> TransactionalEmailDeliveryHandler:
    return TransactionalEmailDeliveryHandler(
        session_factory=SessionFactory,
        settings=SETTINGS,
        transport_factory=transport.factory,
        now=lambda: now,
    )


def run_email(delivery_id: int, now: datetime, transport: FakeTransport) -> None:
    email_handler(transport, now)(email_snapshot(delivery_id))


def queue(graph: DeletionGraph, event, key: str, *, available_at: datetime | None = None) -> int:
    with SessionFactory() as session:
        queued = queue_workspace_email(
            session,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            event_type=event,
            idempotency_key=key,
            available_at=available_at,
        )
        session.commit()
        return queued.delivery.id


def set_user(graph: DeletionGraph, **values: object) -> None:
    with SessionFactory() as session:
        session.execute(update(User).where(User.id == graph.administrator_id).values(**values))
        session.commit()


def workspace_jobs(workspace_id: int) -> list[BackgroundJob]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.workspace_id == workspace_id)
                .order_by(BackgroundJob.id)
            )
        )
        for row in rows:
            session.expunge(row)
        return rows


def add_job(workspace_id: int, status: BackgroundJobStatus) -> int:
    with SessionFactory() as session:
        job = BackgroundJob(
            workspace_id=workspace_id,
            job_type=BackgroundJobType.EXPIRED_SESSION_CLEANUP,
            payload={},
            status=status,
            attempt_count=1 if status is BackgroundJobStatus.RETRY_SCHEDULED else 0,
            available_at=datetime.now(UTC),
        )
        session.add(job)
        session.commit()
        return job.id


def job_status(job_id: int) -> BackgroundJobStatus:
    with SessionFactory() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        return job.status


def assert_nothing_sent_and_canceled(transport: FakeTransport, delivery_id: int) -> None:
    assert transport.sent == []
    row = delivery_row(delivery_id)
    assert row.status is STATUS.CANCELED
    assert row.attempt_count == 0


# Confirmation -----------------------------------------------------------


def test_confirmation_sends_to_current_normalized_email_with_both_dates(graphs) -> None:
    graph = graphs(pending=False)
    now = datetime.now(UTC)
    state = request(graph, now=now)
    confirmation = one(graph.workspace_id, REQUESTED)
    new_address = f"changed-{uuid4().hex[:10]}@synthetic.invalid"
    set_user(graph, email=new_address, normalized_email=new_address)
    transport = FakeTransport(outcomes=[accept])

    run_email(confirmation.id, state.requested_at, transport)

    (message,) = transport.sent
    assert message.recipient == new_address
    assert graph.administrator_email.lower() != message.recipient
    assert state.requested_at.date().isoformat() in message.text_body
    assert state.scheduled_for.date().isoformat() in message.text_body
    assert state.scheduled_for.date().isoformat() in message.html_body
    assert delivery_row(confirmation.id).status is STATUS.SENT


def test_confirmation_with_current_generation_key_sends_on_pending_workspace(graphs) -> None:
    graph = graphs(pending=True)
    delivery_id = queue(
        graph,
        REQUESTED,
        deletion_requested_key(graph.workspace_id, graph.requested_at),
        available_at=graph.requested_at,
    )
    transport = FakeTransport(outcomes=[accept])

    run_email(delivery_id, graph.requested_at + timedelta(hours=1), transport)

    assert len(transport.sent) == 1
    assert delivery_row(delivery_id).status is STATUS.SENT


# Reminder ---------------------------------------------------------------


def test_early_reminder_claim_defers_without_attempt_or_send(graphs) -> None:
    graph = graphs(pending=False)
    state = request(graph, now=datetime.now(UTC))
    reminder = one(graph.workspace_id, REMINDER)
    reminder_at = state.scheduled_for - timedelta(days=7)
    early = reminder_at - timedelta(minutes=1)
    job_id = job_for(reminder.id).id
    # Simulate an early claim: make the reminder job claimable and first in line.
    with SessionFactory() as session:
        session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .values(available_at=state.requested_at, priority=20_000)
        )
        session.commit()
    transport = FakeTransport()

    result = execute_one_background_job(
        session_factory=SessionFactory,
        worker_id=WORKER,
        lease_duration=LEASE,
        handlers={EMAIL_JOB: email_handler(transport, early)},
        now=lambda: early,
        jitter_ratio=0,
    )

    assert result.job_id == job_id and result.claimed and not result.failed
    assert transport.sent == []
    job = job_for(reminder.id)
    assert job.status is BackgroundJobStatus.PENDING
    assert job.attempt_count == 0
    assert job.available_at == reminder_at
    row = delivery_row(reminder.id)
    assert row.status is STATUS.PENDING
    assert row.attempt_count == 0


def test_reminder_sends_once_at_reminder_time_with_scheduled_date(graphs) -> None:
    graph = graphs(pending=False)
    state = request(graph, now=datetime.now(UTC))
    reminder = one(graph.workspace_id, REMINDER)
    transport = FakeTransport(outcomes=[accept])

    run_email(reminder.id, state.scheduled_for - timedelta(days=7), transport)

    (message,) = transport.sent
    assert message.recipient == graph.administrator_email.lower()
    assert state.scheduled_for.date().isoformat() in message.text_body
    assert delivery_row(reminder.id).status is STATUS.SENT


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(days=1)])
def test_reminder_at_or_after_scheduled_time_is_canceled_without_sending(graphs, offset) -> None:
    graph = graphs(pending=False)
    state = request(graph, now=datetime.now(UTC))
    reminder = one(graph.workspace_id, REMINDER)
    transport = FakeTransport()

    run_email(reminder.id, state.scheduled_for + offset, transport)

    assert_nothing_sent_and_canceled(transport, reminder.id)


# Stale generations ------------------------------------------------------


def test_stale_requested_generation_is_canceled_without_sending(graphs) -> None:
    graph = graphs(pending=True)
    stale_id = queue(
        graph,
        REQUESTED,
        deletion_requested_key(graph.workspace_id, graph.requested_at - timedelta(days=1)),
        available_at=graph.requested_at,
    )
    transport = FakeTransport()

    run_email(stale_id, graph.requested_at + timedelta(hours=1), transport)

    assert_nothing_sent_and_canceled(transport, stale_id)


def test_stale_reminder_generation_is_canceled_without_sending(graphs) -> None:
    graph = graphs(pending=True)
    stale_id = queue(
        graph,
        REMINDER,
        deletion_reminder_key(graph.workspace_id, graph.scheduled_for - timedelta(days=1)),
        available_at=graph.requested_at,
    )
    transport = FakeTransport()

    run_email(stale_id, graph.scheduled_for - timedelta(days=6), transport)

    assert_nothing_sent_and_canceled(transport, stale_id)


def test_current_reminder_generation_sends_on_pending_workspace(graphs) -> None:
    graph = graphs(pending=True)
    delivery_id = queue(
        graph,
        REMINDER,
        deletion_reminder_key(graph.workspace_id, graph.scheduled_for),
        available_at=graph.scheduled_for - timedelta(days=7),
    )
    transport = FakeTransport(outcomes=[accept])

    run_email(delivery_id, graph.scheduled_for - timedelta(days=6), transport)

    assert len(transport.sent) == 1
    assert delivery_row(delivery_id).status is STATUS.SENT


@pytest.mark.parametrize("event", [REQUESTED, REMINDER])
def test_old_job_after_restoration_does_not_send(graphs, event) -> None:
    graph = graphs(pending=False)
    now = datetime.now(UTC)
    state = request(graph, now=now)
    delivery = one(graph.workspace_id, event)
    snapshot = email_snapshot(delivery.id)
    cancel(graph, now=now + timedelta(hours=1))
    transport = FakeTransport()

    email_handler(transport, state.scheduled_for - timedelta(days=6))(snapshot)

    assert_nothing_sent_and_canceled(transport, delivery.id)


# Cancellation notice ----------------------------------------------------


def test_cancellation_notice_sends_while_workspace_is_active(graphs) -> None:
    graph = graphs(pending=False)
    now = datetime.now(UTC)
    request(graph, now=now)
    cancel_time = now + timedelta(hours=1)
    cancel(graph, now=cancel_time)
    notice = one(graph.workspace_id, CANCELED)
    transport = FakeTransport(outcomes=[accept])

    run_email(notice.id, cancel_time, transport)

    (message,) = transport.sent
    assert message.recipient == graph.administrator_email.lower()
    assert "canceled" in message.subject
    assert delivery_row(notice.id).status is STATUS.SENT


def test_cancellation_notice_is_canceled_when_workspace_is_pending_again(graphs) -> None:
    graph = graphs(pending=True)
    notice_id = queue(
        graph,
        CANCELED,
        deletion_canceled_key(graph.workspace_id, graph.requested_at - timedelta(days=3)),
    )
    transport = FakeTransport()

    run_email(notice_id, datetime.now(UTC), transport)

    assert_nothing_sent_and_canceled(transport, notice_id)


# Ineligible recipients --------------------------------------------------

INELIGIBLE = {
    "inactive": {"is_active": False},
    "unverified": {"email_verified_at": None},
    "no_normalized_email": {"normalized_email": None},
}


@pytest.mark.parametrize("condition", list(INELIGIBLE))
@pytest.mark.parametrize("event", [REQUESTED, REMINDER, CANCELED])
def test_scoped_deletion_events_are_canceled_for_ineligible_administrator(
    graphs, event, condition
) -> None:
    graph = graphs(pending=False)
    now = datetime.now(UTC)
    state = request(graph, now=now)
    send_time = state.scheduled_for - timedelta(days=6)

    if event is CANCELED:
        send_time = now + timedelta(hours=1)
        cancel(graph, now=send_time)

    delivery = one(graph.workspace_id, event)
    set_user(graph, **INELIGIBLE[condition])
    transport = FakeTransport()

    run_email(delivery.id, send_time, transport)

    assert_nothing_sent_and_canceled(transport, delivery.id)


# Other event types ------------------------------------------------------


def test_password_reset_queued_before_request_still_sends_during_grace_period(graphs) -> None:
    graph = graphs(pending=False)
    reset_id = queue(graph, PASSWORD_RESET, f"routine-reset-{graph.workspace_id}")
    now = datetime.now(UTC)
    request(graph, now=now)

    assert delivery_row(reset_id).status is STATUS.PENDING
    assert job_status(job_for(reset_id).id) is BackgroundJobStatus.PENDING
    transport = FakeTransport(outcomes=[accept])

    run_email(reset_id, now + timedelta(hours=1), transport)

    (message,) = transport.sent
    assert message.recipient == graph.administrator_email.lower()
    assert delivery_row(reset_id).status is STATUS.SENT


def test_request_cancels_verification_delivery_and_its_job(graphs) -> None:
    graph = graphs(pending=False)
    verification_id = queue(graph, VERIFICATION, f"routine-verify-{graph.workspace_id}")
    job_id = job_for(verification_id).id

    request(graph, now=datetime.now(UTC))

    assert delivery_row(verification_id).status is STATUS.CANCELED
    assert job_status(job_id) is BackgroundJobStatus.CANCELED


# Ordinary job counts ----------------------------------------------------


def test_request_cancels_queued_jobs_and_leaves_other_workspace_pending(graphs) -> None:
    graph = graphs(pending=False)
    other = graphs(pending=False)
    pending_id = add_job(graph.workspace_id, BackgroundJobStatus.PENDING)
    retry_id = add_job(graph.workspace_id, BackgroundJobStatus.RETRY_SCHEDULED)
    other_id = add_job(other.workspace_id, BackgroundJobStatus.PENDING)

    request(graph, now=datetime.now(UTC))

    assert job_status(pending_id) is BackgroundJobStatus.CANCELED
    assert job_status(retry_id) is BackgroundJobStatus.CANCELED
    assert job_status(other_id) is BackgroundJobStatus.PENDING
    email_jobs = [job for job in workspace_jobs(graph.workspace_id) if job.job_type is EMAIL_JOB]
    assert len(email_jobs) == 2
    assert all(job.status is BackgroundJobStatus.PENDING for job in email_jobs)


# Job payload and durable text -------------------------------------------


def job_json(job_id: int) -> str:
    with SessionFactory() as session:
        return session.scalar(
            text("SELECT row_to_json(j)::text FROM background_jobs j WHERE j.id = :id"),
            {"id": job_id},
        )


def assert_email_jobs_minimal(
    jobs: list[BackgroundJob], graph: DeletionGraph, delivery_ids: set[int]
) -> None:
    assert jobs
    for job in jobs:
        payload = job.payload
        assert set(payload) == {"version", "delivery_id"}
        assert payload["version"] == 1 and payload["delivery_id"] in delivery_ids
        assert job.payload == {"version": 1, "delivery_id": payload["delivery_id"]}
        blob = job_json(job.id).casefold()
        assert "@" not in blob
        assert graph.slug.casefold() not in blob
        assert graph.provider_league_id.casefold() not in blob


def test_request_and_cancel_email_jobs_carry_only_the_delivery_id(graphs) -> None:
    graph = graphs(pending=False)
    now = datetime.now(UTC)
    request(graph, now=now)
    cancel(graph, now=now + timedelta(hours=1))

    rows = deliveries(graph.workspace_id)
    email_jobs = [job for job in workspace_jobs(graph.workspace_id) if job.job_type is EMAIL_JOB]

    assert len(rows) == 3
    assert len(email_jobs) == 3
    assert_email_jobs_minimal(email_jobs, graph, {row.id for row in rows})


def test_final_deletion_completion_job_carries_only_the_delivery_id(graphs) -> None:
    graph = graphs(pending=True)
    job_id = enqueue_deletion_job(graph)
    with SessionFactory() as session:
        claimed = BackgroundJobRepository(session).claim_next_available(
            worker_id=WORKER, lease_duration=LEASE, now=graph.scheduled_for
        )
        assert claimed is not None and claimed.id == job_id
        snapshot = snapshot_background_job(claimed)
        session.commit()
    with SessionFactory() as session:
        execute_workspace_deletion(
            session,
            workspace_id=snapshot.workspace_id,
            job_id=snapshot.id,
            attempt_count=snapshot.attempt_count,
            now=graph.scheduled_for,
            recipient_cipher_factory=deletion_recipient_cipher,
        )
        session.commit()

    (completion,) = completion_deliveries(graph.workspace_id)
    jobs = []
    with SessionFactory() as session:
        for job in session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key
                == transactional_email_job_idempotency_key(completion.id)
            )
        ):
            session.expunge(job)
            jobs.append(job)

    assert_email_jobs_minimal(jobs, graph, {completion.id})
