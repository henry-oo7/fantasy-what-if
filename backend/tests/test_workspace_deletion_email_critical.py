"""Critical FR-20 deletion-email invariants on real PostgreSQL connections.

Every actor uses its own ``SessionFactory`` session, so each is a separate
PostgreSQL connection and transaction. Synchronization is deterministic:

- *Lock-wait barriers*: one connection holds an uncommitted transaction
  (and its row locks) while another thread blocks on the same rows;
  ``wait_for_lock_waiter`` polls ``pg_stat_activity`` until the second
  connection is observably waiting on a lock before the first commits.
- *In-flight callbacks*: the fake transport runs a competing transaction
  on its own connection while the worker's provider call is "in flight",
  between the worker's committed phase one and its phase three.
- *Thread barriers*: ``threading.Barrier`` starts symmetric races together;
  the database lock order decides the winner and the test asserts the
  invariant for whichever outcome occurred.

No test calls Resend or any network: the transport is an in-memory fake.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.db.auth_models import AuthSession, AuthSessionPurpose, User
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    TransactionalEmailDelivery,
    WorkspaceDeletionRecord,
)
from app.db.session import SessionFactory
from app.notifications.delivery_job_contract import transactional_email_job_idempotency_key
from app.notifications.email_provider import EmailAcceptance, OutgoingEmail
from app.notifications.recipient_encryption import (
    TransactionalEmailKeyringConfigurationError,
    TransactionalEmailRecipientCipher,
    TransactionalEmailRecipientError,
)
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.workspace_deletion_repository import WorkspaceDeletionRepository
from app.services import workspace_deletion_email
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    BackgroundJobSnapshot,
    snapshot_background_job,
)
from app.services.transactional_email_delivery import TransactionalEmailDeliveryHandler
from app.services.transactional_email_intents import queue_workspace_email
from app.services.workspace_deletion import (
    WorkspaceDeletionUnavailableError,
    cancel_workspace_deletion,
    request_workspace_deletion,
)
from app.services.workspace_deletion_email import (
    deletion_canceled_key,
    queue_deletion_request_emails,
)
from app.services.workspace_deletion_execution import (
    WorkspaceDeletionJobHandler,
    WorkspaceDeletionOutcomeKind,
    execute_workspace_deletion,
)
from tests.support.email_delivery import FakeTransport, email_settings, generated_keyring_json
from tests.support.workspace_deletion_graph import (
    ADMIN_PASSWORD,
    DeletionGraph,
    cleanup_graphs,
    completion_deliveries,
    completion_jobs,
    create_deletion_graph,
    deletion_records,
    enqueue_deletion_job,
    load_workspace,
    owned_row_counts,
)

REQUESTED = TransactionalEmailEventType.WORKSPACE_DELETION_REQUESTED
REMINDER = TransactionalEmailEventType.WORKSPACE_DELETION_REMINDER
CANCELED = TransactionalEmailEventType.WORKSPACE_DELETION_CANCELED
COMPLETED = TransactionalEmailEventType.WORKSPACE_DELETION_COMPLETED
VERIFICATION = TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION
PASSWORD_RESET = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET
EMAIL_JOB = BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY
WORKER = "deletion-email-critical"
LEASE = timedelta(minutes=5)

SETTINGS = email_settings(keys=generated_keyring_json())


def cipher_factory() -> TransactionalEmailRecipientCipher:
    return TransactionalEmailRecipientCipher(SETTINGS.transactional_email_keyring())


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


def request(graph: DeletionGraph, session_id: int | None = None, *, now: datetime | None = None):
    with SessionFactory() as session:
        state = request_workspace_deletion(
            session,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            auth_session_id=session_id or standard_session(graph),
            current_password=ADMIN_PASSWORD,
            confirmation_slug=graph.slug,
            now=now,
        )
        session.commit()
        return state


def cancel(
    graph: DeletionGraph, recovery_id: int | None = None, *, now: datetime | None = None
) -> str:
    with SessionFactory() as session:
        cancel_workspace_deletion(
            session,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            auth_session_id=recovery_id or recovery_session(graph),
            current_password=ADMIN_PASSWORD,
            now=now,
        )
        session.commit()
    return "canceled"


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


def by_event(rows, event) -> list[TransactionalEmailDelivery]:
    return [row for row in rows if row.event_type is event]


def job_for(delivery_id: int) -> BackgroundJob | None:
    with SessionFactory() as session:
        job = session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.idempotency_key
                == transactional_email_job_idempotency_key(delivery_id)
            )
            .order_by(BackgroundJob.id.desc())
            .limit(1)
        )
        if job is not None:
            session.expunge(job)
        return job


def jobs(workspace_id: int) -> list[BackgroundJob]:
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


def queue_scoped(graph: DeletionGraph, event, key: str) -> int:
    with SessionFactory() as session:
        queued = queue_workspace_email(
            session,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            event_type=event,
            idempotency_key=key,
        )
        session.commit()
        return queued.delivery.id


def enqueue_sync_job(workspace_id: int) -> int:
    with SessionFactory() as session:
        job = (
            BackgroundJobRepository(session)
            .enqueue(
                workspace_id=workspace_id,
                job_type=BackgroundJobType.EXPIRED_SESSION_CLEANUP,
                payload={},
            )
            .job
        )
        session.commit()
        return job.id


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


def claim(job_id: int, *, now: datetime) -> BackgroundJobSnapshot:
    with SessionFactory() as session:
        job = BackgroundJobRepository(session).claim_next_available(
            worker_id=WORKER, lease_duration=LEASE, now=now
        )
        assert job is not None and job.id == job_id
        snapshot = snapshot_background_job(job)
        session.commit()
    return snapshot


def wait_for_lock_waiter(timeout: float = 10.0) -> None:
    """Block until another connection in this database is waiting on a lock."""

    deadline = time.monotonic() + timeout
    with SessionFactory() as session:
        while time.monotonic() < deadline:
            waiting = session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                    "AND pid <> pg_backend_pid()"
                )
            )
            session.rollback()
            if waiting:
                return
            time.sleep(0.02)
    raise AssertionError("No connection began waiting on the held lock.")


def run_in_thread(actor: Callable[[], object]):
    executor = ThreadPoolExecutor(max_workers=1)
    return executor, executor.submit(actor)


def run_concurrently(*actors: Callable[[], object]) -> list[object]:
    barrier = threading.Barrier(len(actors))

    def synchronized(actor: Callable[[], object]) -> object:
        barrier.wait(timeout=10)
        try:
            return actor()
        except Exception as exc:  # noqa: BLE001 - outcomes are asserted
            return exc

    with ThreadPoolExecutor(max_workers=len(actors)) as executor:
        return list(executor.map(synchronized, actors))


def accept(message: OutgoingEmail) -> EmailAcceptance:
    return EmailAcceptance(provider_message_id=f"synthetic-{message.idempotency_key}")


def durable_text(workspace_id: int, delivery_ids: list[int]) -> str:
    """Every durable row that could hold the recipient, as JSON text."""

    with SessionFactory() as session:
        parts = [
            session.scalar(
                text(
                    "SELECT coalesce(string_agg(row_to_json(d)::text, ' '), '') "
                    "FROM transactional_email_deliveries d WHERE d.id = ANY(:ids)"
                ),
                {"ids": delivery_ids},
            ),
            session.scalar(
                text(
                    "SELECT coalesce(string_agg(row_to_json(j)::text, ' '), '') "
                    "FROM background_jobs j WHERE j.job_type = 'transactional_email_delivery' "
                    "AND (j.workspace_id = :ws OR j.idempotency_key = ANY(:keys))"
                ),
                {
                    "ws": workspace_id,
                    "keys": [transactional_email_job_idempotency_key(i) for i in delivery_ids],
                },
            ),
            session.scalar(
                text(
                    "SELECT coalesce(string_agg(row_to_json(r)::text, ' '), '') "
                    "FROM workspace_deletion_records r WHERE r.former_workspace_id = :ws"
                ),
                {"ws": workspace_id},
            ),
        ]
    return " ".join(parts)


def unscoped_email_job_ids() -> set[int]:
    with SessionFactory() as session:
        return set(
            session.scalars(
                select(BackgroundJob.id).where(
                    BackgroundJob.workspace_id.is_(None), BackgroundJob.job_type == EMAIL_JOB
                )
            )
        )


def assert_scoped_shape(delivery: TransactionalEmailDelivery, graph: DeletionGraph) -> None:
    assert delivery.scope is TransactionalEmailScope.WORKSPACE
    assert delivery.workspace_id == graph.workspace_id
    assert delivery.user_id == graph.administrator_id
    assert delivery.encrypted_recipient is None
    assert delivery.recipient_key_version is None
    job = job_for(delivery.id)
    assert job is not None and job.workspace_id == graph.workspace_id
    assert job.payload == {"version": 1, "delivery_id": delivery.id}


# Request transaction ----------------------------------------------------


def test_request_creates_intents_and_applies_explicit_job_rules_atomically(graphs) -> None:
    graph = graphs(pending=False)
    other = graphs(pending=False)
    stale_verification = queue_scoped(graph, VERIFICATION, f"crit-verify-{graph.workspace_id}")
    open_reset = queue_scoped(graph, PASSWORD_RESET, f"crit-reset-{graph.workspace_id}")
    stale_cancel_notice = queue_scoped(
        graph, CANCELED, deletion_canceled_key(graph.workspace_id, datetime(2026, 1, 1, tzinfo=UTC))
    )
    sync_job = enqueue_sync_job(graph.workspace_id)
    other_reset = queue_scoped(other, PASSWORD_RESET, f"crit-reset-{other.workspace_id}")
    other_cancel_notice = queue_scoped(other, CANCELED, f"crit-cancel-{other.workspace_id}")
    other_sync = enqueue_sync_job(other.workspace_id)
    transport = FakeTransport()

    now = datetime.now(UTC)
    state = request(graph, now=now)

    assert transport.sent == []
    rows = deliveries(graph.workspace_id)
    (confirmation,) = by_event(rows, REQUESTED)
    (reminder,) = by_event(rows, REMINDER)
    for delivery in (confirmation, reminder):
        assert delivery.status is TransactionalEmailDeliveryStatus.PENDING
        assert_scoped_shape(delivery, graph)
        assert job_for(delivery.id).status is BackgroundJobStatus.PENDING
    assert confirmation.available_at == now
    assert reminder.available_at == state.scheduled_for - timedelta(days=7)
    assert job_for(reminder.id).available_at == state.scheduled_for - timedelta(days=7)

    by_id = {row.id: row for row in rows}
    # Stale identity and earlier deletion-lifecycle intents: delivery and job canceled.
    for delivery_id in (stale_verification, stale_cancel_notice):
        assert by_id[delivery_id].status is TransactionalEmailDeliveryStatus.CANCELED
        assert job_for(delivery_id).status is BackgroundJobStatus.CANCELED
    # Recovery stays available in the grace period: reset is untouched.
    assert by_id[open_reset].status is TransactionalEmailDeliveryStatus.PENDING
    assert job_for(open_reset).status is BackgroundJobStatus.PENDING
    # Ordinary workspace work stops; exactly one deletion job is scheduled.
    workspace_jobs = {job.id: job for job in jobs(graph.workspace_id)}
    assert workspace_jobs[sync_job].status is BackgroundJobStatus.CANCELED
    deletion_jobs = [
        job
        for job in workspace_jobs.values()
        if job.job_type is BackgroundJobType.WORKSPACE_DELETION
        and job.status is BackgroundJobStatus.PENDING
    ]
    assert len(deletion_jobs) == 1 and deletion_jobs[0].available_at == state.scheduled_for

    # Another workspace is never touched.
    other_rows = {row.id: row for row in deliveries(other.workspace_id)}
    assert other_rows[other_reset].status is TransactionalEmailDeliveryStatus.PENDING
    assert other_rows[other_cancel_notice].status is TransactionalEmailDeliveryStatus.PENDING
    assert job_for(other_cancel_notice).status is BackgroundJobStatus.PENDING
    assert {job.id: job for job in jobs(other.workspace_id)}[
        other_sync
    ].status is BackgroundJobStatus.PENDING
    assert by_event(deliveries(other.workspace_id), REQUESTED) == []


@pytest.mark.parametrize("failure", ["confirmation", "reminder", "email_job"])
def test_request_rolls_back_entirely_when_an_intent_cannot_be_created(
    graphs, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    graph = graphs(pending=False)
    open_reset = queue_scoped(graph, PASSWORD_RESET, f"crit-reset-{graph.workspace_id}")
    sync_job = enqueue_sync_job(graph.workspace_id)
    session_id = standard_session(graph)
    real_queue = workspace_deletion_email.queue_workspace_email
    real_enqueue = BackgroundJobRepository.enqueue

    def failing_queue(session, **kwargs):
        target = REQUESTED if failure == "confirmation" else REMINDER
        if kwargs["event_type"] is target:
            raise RuntimeError("synthetic intent failure")
        return real_queue(session, **kwargs)

    def failing_enqueue(self, **kwargs):
        if kwargs["job_type"] is EMAIL_JOB:
            raise RuntimeError("synthetic job failure")
        return real_enqueue(self, **kwargs)

    if failure == "email_job":
        monkeypatch.setattr(BackgroundJobRepository, "enqueue", failing_enqueue)
    else:
        monkeypatch.setattr(workspace_deletion_email, "queue_workspace_email", failing_queue)

    with pytest.raises(RuntimeError, match="synthetic"):
        request(graph, session_id)

    workspace = load_workspace(graph.workspace_id)
    assert workspace.registration_status is WorkspaceRegistrationStatus.ACTIVE
    assert workspace.deletion_requested_at is None and workspace.deletion_scheduled_for is None
    with SessionFactory() as session:
        auth = session.get(AuthSession, session_id)
        assert auth.revoked_at is None
    rows = deliveries(graph.workspace_id)
    assert [row.id for row in rows] == [open_reset]
    assert rows[0].status is TransactionalEmailDeliveryStatus.PENDING
    assert job_for(open_reset).status is BackgroundJobStatus.PENDING
    workspace_jobs = jobs(graph.workspace_id)
    assert {job.job_type for job in workspace_jobs} == {
        BackgroundJobType.EXPIRED_SESSION_CLEANUP,
        EMAIL_JOB,
    }
    assert {job.id: job for job in workspace_jobs}[sync_job].status is BackgroundJobStatus.PENDING


def test_concurrent_and_repeated_requests_keep_one_generation(graphs) -> None:
    graph = graphs(pending=False)
    first, second = standard_session(graph), standard_session(graph)

    results = run_concurrently(lambda: request(graph, first), lambda: request(graph, second))
    assert not any(isinstance(result, Exception) for result in results), results
    assert results[0].scheduled_for == results[1].scheduled_for
    # A later repeat from a revoked-by-request session observes the schedule.
    repeat = request(graph, first)
    assert repeat.scheduled_for == results[0].scheduled_for

    rows = deliveries(graph.workspace_id)
    assert len(by_event(rows, REQUESTED)) == 1
    assert len(by_event(rows, REMINDER)) == 1
    email_jobs = [job for job in jobs(graph.workspace_id) if job.job_type is EMAIL_JOB]
    assert len(email_jobs) == 2
    assert all(job.status is BackgroundJobStatus.PENDING for job in email_jobs)


# Cancellation transaction -----------------------------------------------


def test_cancellation_suppresses_request_intents_and_queues_notice(graphs) -> None:
    graph = graphs(pending=False)
    other = graphs(pending=False)
    request(graph)
    request(other)
    recovery_id = recovery_session(graph)

    cancel(graph, recovery_id)

    rows = deliveries(graph.workspace_id)
    for event in (REQUESTED, REMINDER):
        (delivery,) = by_event(rows, event)
        assert delivery.status is TransactionalEmailDeliveryStatus.CANCELED
        assert job_for(delivery.id).status is BackgroundJobStatus.CANCELED
    (notice,) = by_event(rows, CANCELED)
    assert notice.status is TransactionalEmailDeliveryStatus.PENDING
    assert_scoped_shape(notice, graph)
    assert job_for(notice.id).status is BackgroundJobStatus.PENDING

    workspace = load_workspace(graph.workspace_id)
    assert workspace.registration_status is WorkspaceRegistrationStatus.ACTIVE
    with SessionFactory() as session:
        assert session.get(AuthSession, recovery_id).revoked_at is not None
        live = session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == graph.administrator_id, AuthSession.revoked_at.is_(None)
            )
        ).all()
        assert live == []  # the administrator must sign in again

    # The other workspace's pending deletion intents are untouched.
    for delivery in deliveries(other.workspace_id):
        assert delivery.status is TransactionalEmailDeliveryStatus.PENDING
        assert job_for(delivery.id).status is BackgroundJobStatus.PENDING


def test_cancellation_rolls_back_when_the_notice_cannot_be_created(
    graphs, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = graphs(pending=False)
    request(graph)
    recovery_id = recovery_session(graph)
    real_queue = workspace_deletion_email.queue_workspace_email

    def failing_queue(session, **kwargs):
        if kwargs["event_type"] is CANCELED:
            raise RuntimeError("synthetic notice failure")
        return real_queue(session, **kwargs)

    monkeypatch.setattr(workspace_deletion_email, "queue_workspace_email", failing_queue)

    with pytest.raises(RuntimeError, match="synthetic"):
        cancel(graph, recovery_id)

    workspace = load_workspace(graph.workspace_id)
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    with SessionFactory() as session:
        assert session.get(AuthSession, recovery_id).revoked_at is None
    rows = deliveries(graph.workspace_id)
    assert by_event(rows, CANCELED) == []
    for event in (REQUESTED, REMINDER):
        (delivery,) = by_event(rows, event)
        assert delivery.status is TransactionalEmailDeliveryStatus.PENDING
        assert job_for(delivery.id).status is BackgroundJobStatus.PENDING
    assert any(
        job.job_type is BackgroundJobType.WORKSPACE_DELETION
        and job.status is BackgroundJobStatus.PENDING
        for job in jobs(graph.workspace_id)
    )


def test_cancel_then_request_again_creates_a_fresh_generation_without_reviving(graphs) -> None:
    graph = graphs(pending=False)
    first = request(graph)
    first_rows = deliveries(graph.workspace_id)
    old_confirmation = by_event(first_rows, REQUESTED)[0]
    cancel(graph)
    old_notice = by_event(deliveries(graph.workspace_id), CANCELED)[0]
    old_notice_job = email_snapshot(old_notice.id)

    second = request(graph)

    assert second.requested_at > first.requested_at
    rows = deliveries(graph.workspace_id)
    confirmations = by_event(rows, REQUESTED)
    reminders = by_event(rows, REMINDER)
    assert len(confirmations) == 2 and len(reminders) == 2
    live = [row for row in rows if row.status is TransactionalEmailDeliveryStatus.PENDING]
    assert {row.event_type for row in live} == {REQUESTED, REMINDER}
    assert len({row.idempotency_key for row in confirmations + reminders}) == 4
    # The previous cancellation notice is stale once deletion is requested again.
    assert {row.id: row for row in rows}[
        old_notice.id
    ].status is TransactionalEmailDeliveryStatus.CANCELED

    # A worker holding the old notice's job sends nothing and revives nothing.
    transport = FakeTransport()
    email_handler(transport, datetime.now(UTC))(old_notice_job)
    assert transport.sent == []
    rows_after = {row.id: row for row in deliveries(graph.workspace_id)}
    assert rows_after[old_notice.id].status is TransactionalEmailDeliveryStatus.CANCELED
    assert rows_after[old_confirmation.id].status is TransactionalEmailDeliveryStatus.CANCELED


# Worker races -----------------------------------------------------------


def test_request_holding_locks_beats_worker_on_stale_identity_email(graphs) -> None:
    """Race 1, lock order: request transaction first, worker blocks then sees cancel."""

    graph = graphs(pending=False)
    stale = queue_scoped(
        graph, CANCELED, deletion_canceled_key(graph.workspace_id, datetime(2026, 1, 1, tzinfo=UTC))
    )
    snapshot = email_snapshot(stale)
    session_id = standard_session(graph)
    transport = FakeTransport(outcomes=[accept])

    holder = SessionFactory()
    try:
        request_workspace_deletion(
            holder,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            auth_session_id=session_id,
            current_password=ADMIN_PASSWORD,
            confirmation_slug=graph.slug,
        )
        executor, future = run_in_thread(
            lambda: email_handler(transport, datetime.now(UTC))(snapshot)
        )
        wait_for_lock_waiter()
        holder.commit()
        future.result(timeout=10)
        executor.shutdown()
    finally:
        holder.close()

    assert transport.sent == []
    stale_row = {row.id: row for row in deliveries(graph.workspace_id)}[stale]
    assert stale_row.status is TransactionalEmailDeliveryStatus.CANCELED
    assert stale_row.attempt_count == 0


def test_request_during_in_flight_stale_email_discards_the_outcome(graphs) -> None:
    """Race 1, other order: the worker's attempt is in flight when deletion commits."""

    graph = graphs(pending=False)
    stale = queue_scoped(
        graph, CANCELED, deletion_canceled_key(graph.workspace_id, datetime(2026, 1, 1, tzinfo=UTC))
    )
    snapshot = email_snapshot(stale)

    def request_mid_flight(message: OutgoingEmail) -> EmailAcceptance:
        request(graph)
        return accept(message)

    transport = FakeTransport(outcomes=[request_mid_flight])
    email_handler(transport, datetime.now(UTC))(snapshot)

    assert len(transport.sent) == 1  # already in flight; cannot be recalled
    stale_row = {row.id: row for row in deliveries(graph.workspace_id)}[stale]
    assert stale_row.status is TransactionalEmailDeliveryStatus.CANCELED
    assert stale_row.sent_at is None and stale_row.provider_message_id is None


def test_cancellation_holding_locks_beats_reminder_worker_before_cutoff(graphs) -> None:
    """Race 2: cancellation commits first; the due reminder never sends."""

    graph = graphs(pending=False)
    state = request(graph)
    (reminder,) = by_event(deliveries(graph.workspace_id), REMINDER)
    snapshot = email_snapshot(reminder.id)
    reminder_now = state.scheduled_for - timedelta(days=7)
    recovery_id = recovery_session(graph)
    transport = FakeTransport(outcomes=[accept])

    holder = SessionFactory()
    try:
        cancel_workspace_deletion(
            holder,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            auth_session_id=recovery_id,
            current_password=ADMIN_PASSWORD,
        )
        executor, future = run_in_thread(lambda: email_handler(transport, reminder_now)(snapshot))
        wait_for_lock_waiter()
        holder.commit()
        future.result(timeout=10)
        executor.shutdown()
    finally:
        holder.close()

    assert transport.sent == []
    row = {r.id: r for r in deliveries(graph.workspace_id)}[reminder.id]
    assert row.status is TransactionalEmailDeliveryStatus.CANCELED
    assert row.attempt_count == 0


@pytest.mark.parametrize("event", [REQUESTED, REMINDER])
def test_cancellation_while_provider_call_in_flight_wins(graphs, event) -> None:
    """Race 3: a running job is not rewritten; its stale outcome is ignored."""

    graph = graphs(pending=False)
    state = request(graph)
    (delivery,) = by_event(deliveries(graph.workspace_id), event)
    snapshot = email_snapshot(delivery.id)
    send_now = state.requested_at if event is REQUESTED else delivery.available_at

    def cancel_mid_flight(message: OutgoingEmail) -> EmailAcceptance:
        cancel(graph)
        return accept(message)

    transport = FakeTransport(outcomes=[cancel_mid_flight])
    email_handler(transport, send_now)(snapshot)

    assert len(transport.sent) == 1
    row = {r.id: r for r in deliveries(graph.workspace_id)}[delivery.id]
    assert row.status is TransactionalEmailDeliveryStatus.CANCELED
    assert row.sent_at is None and row.provider_message_id is None
    assert load_workspace(graph.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.ACTIVE
    )
    assert len(by_event(deliveries(graph.workspace_id), CANCELED)) == 1


# Final deletion transaction ---------------------------------------------


def pending_graph_with_intents(graphs) -> tuple[DeletionGraph, BackgroundJobSnapshot]:
    graph = graphs()
    with SessionFactory() as session:
        queue_deletion_request_emails(
            session,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            requested_at=graph.requested_at,
            scheduled_for=graph.scheduled_for,
        )
        session.commit()
    job_id = enqueue_deletion_job(graph)
    return graph, claim(job_id, now=graph.scheduled_for)


def delete_now(
    snapshot: BackgroundJobSnapshot, now: datetime, factory=cipher_factory
) -> WorkspaceDeletionOutcomeKind:
    with SessionFactory() as session:
        outcome = execute_workspace_deletion(
            session,
            workspace_id=snapshot.workspace_id,
            job_id=snapshot.id,
            attempt_count=snapshot.attempt_count,
            now=now,
            recipient_cipher_factory=factory,
        )
        session.commit()
    return outcome


def test_final_deletion_creates_one_encrypted_unscoped_completion_intent(graphs) -> None:
    graph, snapshot = pending_graph_with_intents(graphs)
    scoped_ids = [row.id for row in deliveries(graph.workspace_id)]
    assert len(scoped_ids) == 2

    assert delete_now(snapshot, graph.scheduled_for) is WorkspaceDeletionOutcomeKind.DELETED

    (completion,) = completion_deliveries(graph.workspace_id)
    assert completion.scope is TransactionalEmailScope.UNSCOPED
    assert completion.event_type is COMPLETED
    assert completion.workspace_id is None and completion.user_id is None
    assert completion.status is TransactionalEmailDeliveryStatus.PENDING
    assert completion.available_at == graph.scheduled_for
    assert completion.recipient_key_version == 1
    assert completion.encrypted_recipient
    assert graph.administrator_email.lower() not in completion.encrypted_recipient.lower()
    assert (
        cipher_factory().decrypt(
            ciphertext=completion.encrypted_recipient,
            key_version=completion.recipient_key_version,
            delivery_id=completion.id,
        )
        == graph.administrator_email.lower()
    )
    for forbidden in (graph.slug, graph.provider_league_id, graph.display_name):
        assert forbidden not in completion.idempotency_key

    (job,) = completion_jobs(completion.id)
    assert job.workspace_id is None
    assert job.job_type is EMAIL_JOB
    assert job.status is BackgroundJobStatus.PENDING
    assert job.payload == {"version": 1, "delivery_id": completion.id}

    # Tenant graph, scoped deliveries, and tenant jobs are gone; the
    # completion delivery and its job survived the workspace/user cascade.
    assert load_workspace(graph.workspace_id) is None
    assert all(count == 0 for count in owned_row_counts(graph).values())
    assert deliveries(graph.workspace_id) == []
    with SessionFactory() as session:
        assert (
            session.scalar(
                select(TransactionalEmailDelivery.id).where(
                    TransactionalEmailDelivery.id.in_(scoped_ids)
                )
            )
            is None
        )
    assert len(deletion_records(graph.workspace_id)) == 1

    # No plaintext recipient in any durable delivery, job, or record row.
    blob = durable_text(graph.workspace_id, [completion.id, *scoped_ids]).lower()
    assert graph.administrator_email.lower() not in blob
    assert graph.slug not in blob


class _FailingCipher:
    def encrypt(self, **_kwargs):
        raise TransactionalEmailRecipientError("synthetic encryption failure")


def _missing_keyring() -> TransactionalEmailRecipientCipher:
    raise TransactionalEmailKeyringConfigurationError("synthetic missing keyring")


@pytest.mark.parametrize("failure", ["keyring", "encrypt", "delivery", "job", "after_intent"])
def test_final_deletion_rolls_back_with_its_completion_intent(
    graphs, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    graph, snapshot = pending_graph_with_intents(graphs)
    before = owned_row_counts(graph)
    unscoped_before = unscoped_email_job_ids()
    factory: Callable[[], object] = cipher_factory

    if failure == "keyring":
        factory = _missing_keyring
    elif failure == "encrypt":
        factory = _FailingCipher
    elif failure == "delivery":
        monkeypatch.setattr(
            workspace_deletion_email,
            "queue_unscoped_completion_email",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic delivery failure")),
        )
    elif failure == "job":
        real_enqueue = BackgroundJobRepository.enqueue

        def failing_enqueue(self, **kwargs):
            if kwargs["job_type"] is EMAIL_JOB:
                raise RuntimeError("synthetic job failure")
            return real_enqueue(self, **kwargs)

        monkeypatch.setattr(BackgroundJobRepository, "enqueue", failing_enqueue)
    else:

        def fail_delete(self, workspace_id: int) -> int:
            raise RuntimeError("synthetic late failure")

        monkeypatch.setattr(WorkspaceDeletionRepository, "delete_workspace", fail_delete)

    with pytest.raises((BackgroundJobExecutionError, RuntimeError)):
        WorkspaceDeletionJobHandler(
            session_factory=SessionFactory,
            recipient_cipher_factory=factory,
            now=lambda: graph.scheduled_for,
        )(snapshot)

    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    assert owned_row_counts(graph) == before
    assert deletion_records(graph.workspace_id) == []
    assert completion_deliveries(graph.workspace_id) == []
    assert unscoped_email_job_ids() == unscoped_before
    with SessionFactory() as session:
        # Claim, slug, email, and credentials remain reserved.
        assert session.get(User, graph.administrator_id) is not None
    for delivery in deliveries(graph.workspace_id):
        assert delivery.status is TransactionalEmailDeliveryStatus.PENDING


def test_completion_intent_is_invisible_until_final_deletion_commits(
    graphs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Race 5: another connection never observes the intent before commit."""

    graph, snapshot = pending_graph_with_intents(graphs)
    unscoped_before = unscoped_email_job_ids()
    paused = threading.Event()
    release = threading.Event()
    real_delete = WorkspaceDeletionRepository.delete_workspace

    def pausing_delete(self, workspace_id: int) -> int:
        paused.set()
        assert release.wait(timeout=10)
        return real_delete(self, workspace_id)

    monkeypatch.setattr(WorkspaceDeletionRepository, "delete_workspace", pausing_delete)
    executor, future = run_in_thread(lambda: delete_now(snapshot, graph.scheduled_for))
    assert paused.wait(timeout=10)

    # Intent flushed inside the open deletion transaction: not visible elsewhere.
    assert completion_deliveries(graph.workspace_id) == []
    assert unscoped_email_job_ids() == unscoped_before
    assert load_workspace(graph.workspace_id) is not None

    release.set()
    assert future.result(timeout=10) is WorkspaceDeletionOutcomeKind.DELETED
    executor.shutdown()
    (completion,) = completion_deliveries(graph.workspace_id)
    assert len(completion_jobs(completion.id)) == 1


def test_two_final_deletion_workers_create_exactly_one_completion_intent(graphs) -> None:
    """Race 6: the workspace lock serializes; the loser is a no-op or claim loss."""

    graph, snapshot = pending_graph_with_intents(graphs)

    results = run_concurrently(
        lambda: delete_now(snapshot, graph.scheduled_for),
        lambda: delete_now(snapshot, graph.scheduled_for),
    )

    assert WorkspaceDeletionOutcomeKind.DELETED in results
    for result in results:
        assert result in (
            WorkspaceDeletionOutcomeKind.DELETED,
            WorkspaceDeletionOutcomeKind.ALREADY_COMPLETED,
        ) or isinstance(result, BackgroundJobExecutionError), result
    assert results.count(WorkspaceDeletionOutcomeKind.DELETED) == 1
    (completion,) = completion_deliveries(graph.workspace_id)
    assert len(completion_jobs(completion.id)) == 1
    assert len(deletion_records(graph.workspace_id)) == 1


@pytest.mark.parametrize("holder", ["cancellation", "deletion"])
def test_cancellation_versus_final_deletion_at_the_cutoff(graphs, holder: str) -> None:
    """Race 4: before cutoff cancellation can win; at cutoff deletion wins.

    The holder takes the workspace lock first in an uncommitted transaction;
    the other actor blocks and resolves against the committed outcome.
    """

    graph, snapshot = pending_graph_with_intents(graphs)
    before_cutoff = graph.scheduled_for - timedelta(microseconds=1)
    recovery_id = recovery_session(graph, now=before_cutoff - timedelta(minutes=1))
    session = SessionFactory()

    try:
        if holder == "cancellation":
            cancel_workspace_deletion(
                session,
                workspace_id=graph.workspace_id,
                user_id=graph.administrator_id,
                auth_session_id=recovery_id,
                current_password=ADMIN_PASSWORD,
                now=before_cutoff,
            )
            executor, future = run_in_thread(lambda: delete_now(snapshot, graph.scheduled_for))
        else:
            execute_workspace_deletion(
                session,
                workspace_id=graph.workspace_id,
                job_id=snapshot.id,
                attempt_count=snapshot.attempt_count,
                now=graph.scheduled_for,
                recipient_cipher_factory=cipher_factory,
            )
            executor, future = run_in_thread(lambda: cancel(graph, recovery_id, now=before_cutoff))
        wait_for_lock_waiter()
        session.commit()
        try:
            other = future.result(timeout=10)
        except Exception as exc:  # noqa: BLE001 - outcome asserted below
            other = exc
        executor.shutdown()
    finally:
        session.close()

    if holder == "cancellation":
        assert other is WorkspaceDeletionOutcomeKind.NOT_PENDING
        assert load_workspace(graph.workspace_id) is not None
        assert completion_deliveries(graph.workspace_id) == []
        rows = deliveries(graph.workspace_id)
        assert all(
            row.status is TransactionalEmailDeliveryStatus.CANCELED
            for row in rows
            if row.event_type in (REQUESTED, REMINDER)
        )
        assert len(by_event(rows, CANCELED)) == 1
    else:
        assert isinstance(other, WorkspaceDeletionUnavailableError)
        assert load_workspace(graph.workspace_id) is None
        assert deliveries(graph.workspace_id) == []  # no cancellation notice
        (completion,) = completion_deliveries(graph.workspace_id)
        assert len(completion_jobs(completion.id)) == 1


# Completion delivery and recipient scrubbing ----------------------------


def _deleted_with_completion(graphs) -> tuple[DeletionGraph, TransactionalEmailDelivery]:
    graph, snapshot = pending_graph_with_intents(graphs)
    delete_now(snapshot, graph.scheduled_for)
    (completion,) = completion_deliveries(graph.workspace_id)
    return graph, completion


def _completion_snapshot(delivery_id: int) -> BackgroundJobSnapshot:
    (job,) = completion_jobs(delivery_id)
    return snapshot_background_job(job)


def test_completion_sends_minimum_content_and_scrubs_recipient(graphs) -> None:
    graph, completion = _deleted_with_completion(graphs)
    transport = FakeTransport(outcomes=[accept])

    email_handler(transport, graph.scheduled_for + timedelta(minutes=1))(
        _completion_snapshot(completion.id)
    )

    (message,) = transport.sent
    assert message.recipient == graph.administrator_email.lower()
    body = f"{message.subject}\n{message.text_body}\n{message.html_body}"
    for forbidden in (
        graph.slug,
        graph.provider_league_id,
        graph.display_name,
        graph.administrator_username,
        str(graph.workspace_id),
        str(graph.claim_id),
        "espn",
        "ESPN",
    ):
        assert forbidden not in body
    (row,) = completion_deliveries(graph.workspace_id)
    assert row.status is TransactionalEmailDeliveryStatus.SENT
    assert row.encrypted_recipient is None and row.recipient_key_version is None
    assert load_workspace(graph.workspace_id) is None


def test_permanent_completion_failure_scrubs_and_never_restores_tenant(graphs) -> None:
    from app.notifications.email_provider import EmailFailureCategory, EmailProviderError

    graph, completion = _deleted_with_completion(graphs)
    transport = FakeTransport(outcomes=[EmailProviderError(EmailFailureCategory.RECIPIENT_INVALID)])

    with pytest.raises(BackgroundJobExecutionError):
        email_handler(transport, graph.scheduled_for)(_completion_snapshot(completion.id))

    (row,) = completion_deliveries(graph.workspace_id)
    assert row.status is TransactionalEmailDeliveryStatus.PERMANENTLY_FAILED
    assert row.safe_failure_code
    assert row.encrypted_recipient is None and row.recipient_key_version is None
    assert load_workspace(graph.workspace_id) is None
    assert len(deletion_records(graph.workspace_id)) == 1
    with SessionFactory() as session:
        assert session.get(Installation, graph.workspace_id) is None
        assert (
            session.scalar(
                select(WorkspaceDeletionRecord.id).where(
                    WorkspaceDeletionRecord.former_workspace_id == graph.workspace_id
                )
            )
            is not None
        )


def test_unscoped_completion_job_is_invisible_to_workspace_scoped_operations(graphs) -> None:
    """Cross-workspace isolation: NULL workspace is never a wildcard."""

    graph, completion = _deleted_with_completion(graphs)
    other = graphs(pending=False)
    request(other)
    (job,) = completion_jobs(completion.id)

    with SessionFactory() as session:
        repository = BackgroundJobRepository(session)
        assert repository.get_for_workspace(job_id=job.id, workspace_id=other.workspace_id) is None
        repository.cancel_queued_workspace_jobs(
            workspace_id=other.workspace_id, now=datetime.now(UTC)
        )
        session.commit()

    (still,) = completion_jobs(completion.id)
    assert still.status is BackgroundJobStatus.PENDING
    assert completion.id not in {row.id for row in deliveries(other.workspace_id)}
