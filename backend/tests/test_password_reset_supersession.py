# ruff: noqa: F811 - pytest fixtures are imported from the durability module
"""Critical tests: reset completion permanently removes reset capability.

Contract: a successful reset completion, in one transaction, locks the
administrator and every nonterminal ``administrator_password_reset``
delivery, consumes the token, invalidates all unused reset tokens, revokes
sessions, cancels the open reset deliveries (and their queued jobs), and
queues the password-changed notice. No open reset delivery may mint a
usable token afterward, and a stale worker outcome cannot revive one.

Race tests use real PostgreSQL connections (one session per thread) and
deterministic synchronization: events inside the worker's provider call,
or an observed ungranted PostgreSQL lock. Nothing replaces the database
race with sequential calls or mocks. All data is synthetic.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.security import verify_password
from app.db.auth_models import AuthSession, PasswordResetToken, User
from app.db.platform_enums import (
    BackgroundJobStatus,
    TransactionalEmailDeliveryStatus,
)
from app.db.platform_models import BackgroundJob, TransactionalEmailDelivery
from app.db.session import SessionFactory, engine
from app.notifications.email_provider import EmailAcceptance, OutgoingEmail
from app.repositories.account_token_repository import AccountTokenRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.transactional_email_delivery_repository import (
    TransactionalEmailDeliveryRepository,
)
from app.services.background_job_worker import snapshot_background_job
from app.services.password_recovery import (
    PasswordRecoveryError,
    reset_administrator_password,
)
from app.services.transactional_email_delivery import TransactionalEmailDeliveryHandler
from app.services.transactional_email_intents import queue_workspace_email
from tests.support.email_delivery import FakeTransport, email_settings, token_from
from tests.support.identity_email import (
    PASSWORD_CHANGED,
    PASSWORD_RESET,
    VERIFICATION,
    email_jobs_for,
    identity_deliveries,
    issue_password_reset_token,
)
from tests.test_identity_email_durability import (  # noqa: F401 - fixtures
    NEW_PASSWORD,
    PASSWORD,
    active_admin,
    client,
    create_administrator,
    delete_workspaces,
    isolate_rate_limit_rows,
    no_provider_or_network,
    post,
)

NONTERMINAL = (
    TransactionalEmailDeliveryStatus.PENDING,
    TransactionalEmailDeliveryStatus.SENDING,
    TransactionalEmailDeliveryStatus.RETRY_SCHEDULED,
)


@dataclass
class OpenResets:
    legacy_token: str
    pending_id: int
    retry_id: int
    sending_id: int
    sending_token_id: int


def _queue_reset(session, workspace_id: int, user_id: int):
    queued = queue_workspace_email(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=PASSWORD_RESET,
        idempotency_key=f"test:reset:{user_id}:{uuid4().hex}",
    )
    assert queued.job is not None
    return queued


def seed_open_resets(workspace_id: int, user_id: int) -> OpenResets:
    """Legacy token, plus pending, retry-scheduled, and sending reset
    deliveries; the sending one already has a worker-created token."""

    now = datetime.now(UTC)

    with SessionFactory() as session:
        user = session.get(User, user_id)
        legacy = issue_password_reset_token(session, user=user, lifetime_minutes=30)
        deliveries = TransactionalEmailDeliveryRepository(session)

        pending = _queue_reset(session, workspace_id, user_id)

        retry = _queue_reset(session, workspace_id, user_id)
        attempt = deliveries.begin_attempt(retry.delivery, now=now)
        deliveries.schedule_retry(
            retry.delivery,
            attempt=attempt,
            safe_failure_code="provider_unavailable",
            available_at=now,
        )
        retry.job.status = BackgroundJobStatus.RETRY_SCHEDULED
        retry.job.attempt_count = 1

        sending = _queue_reset(session, workspace_id, user_id)
        attempt = deliveries.begin_attempt(sending.delivery, now=now)
        sending_token = AccountTokenRepository(session).create_delivery_password_reset_token(
            user_id=user_id,
            raw_token=f"synthetic-inflight-{uuid4().hex}",
            expires_at=now + timedelta(minutes=30),
            delivery_id=sending.delivery.id,
            delivery_attempt=attempt,
            now=now,
        )
        sending.job.status = BackgroundJobStatus.RUNNING
        sending.job.attempt_count = 1
        sending.job.claimed_at = now
        sending.job.started_at = now
        sending.job.claimed_by = "synthetic-worker"
        sending.job.lease_expires_at = now + timedelta(minutes=5)
        session.commit()

        return OpenResets(
            legacy_token=legacy.raw_token,
            pending_id=pending.delivery.id,
            retry_id=retry.delivery.id,
            sending_id=sending.delivery.id,
            sending_token_id=sending_token.id,
        )


def open_auth_session(user_id: int) -> int:
    from app.repositories.session_repository import SessionRepository

    with SessionFactory() as session:
        created = SessionRepository(session).replace_user_sessions(
            user=session.get(User, user_id), lifetime_hours=1
        )
        session.commit()
        return created.session.id


def unused_reset_tokens(user_id: int) -> list[PasswordResetToken]:
    with SessionFactory() as session:
        return list(
            session.scalars(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == user_id,
                    PasswordResetToken.used_at.is_(None),
                )
            )
        )


def load(model, row_id: int):
    with SessionFactory() as session:
        return session.get(model, row_id)


def job_for(delivery_id: int) -> BackgroundJob:
    with SessionFactory() as session:
        jobs = email_jobs_for(session, [delivery_id])
        assert len(jobs) == 1
        return jobs[0]


@pytest.fixture
def other_admin() -> Iterator[tuple[int, int, str]]:
    created = create_administrator(provisional=False)
    yield created
    delete_workspaces([created[0]])


# Completion cancels every open reset delivery --------------------------------


def test_completion_cancels_open_resets_and_invalidates_their_tokens(
    client: TestClient,
    active_admin: tuple[int, int, str],
    other_admin: tuple[int, int, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_id, user_id, email = active_admin
    other_workspace, other_user, _ = other_admin
    seeded = seed_open_resets(workspace_id, user_id)

    with SessionFactory() as session:
        # Must stay untouched: another administrator's (other workspace)
        # reset, and this administrator's non-reset deliveries.
        foreign = _queue_reset(session, other_workspace, other_user)
        verification = queue_workspace_email(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type=VERIFICATION,
            idempotency_key=f"test:verification:{user_id}",
        )
        prior_notice = queue_workspace_email(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type=PASSWORD_CHANGED,
            idempotency_key=f"test:changed:{user_id}",
        )
        session.commit()
        foreign_id = foreign.delivery.id
        untouched_ids = [verification.delivery.id, prior_notice.delivery.id]

    auth_session_id = open_auth_session(user_id)
    sending_job_before = job_for(seeded.sending_id)

    with caplog.at_level(logging.DEBUG):
        response = post(
            client,
            "/api/auth/password-reset/complete",
            {"token": seeded.legacy_token, "new_password": NEW_PASSWORD},
        )

    assert response.status_code == status.HTTP_200_OK
    for secret in (seeded.legacy_token, NEW_PASSWORD, email, str(seeded.pending_id)):
        assert secret not in response.text
        assert secret not in caplog.text
    for internal in ("delivery", "job", "canceled", "provider"):
        assert internal not in response.text.lower()

    for delivery_id in (seeded.pending_id, seeded.retry_id, seeded.sending_id):
        delivery = load(TransactionalEmailDelivery, delivery_id)
        assert delivery.status is TransactionalEmailDeliveryStatus.CANCELED
        assert delivery.canceled_at is not None
        assert delivery.retention_expires_at is not None
        assert delivery.safe_failure_code is None

    assert job_for(seeded.pending_id).status is BackgroundJobStatus.CANCELED
    assert job_for(seeded.retry_id).status is BackgroundJobStatus.CANCELED
    # A claimed job is left running; the canceled delivery is authoritative.
    running = job_for(seeded.sending_id)
    assert running.status is BackgroundJobStatus.RUNNING
    assert running.claimed_by == sending_job_before.claimed_by

    # The in-flight attempt's token and every other reset token are dead.
    assert load(PasswordResetToken, seeded.sending_token_id).used_at is not None
    assert unused_reset_tokens(user_id) == []

    with SessionFactory() as session:
        assert verify_password(NEW_PASSWORD, session.get(User, user_id).password_hash)
        assert session.get(AuthSession, auth_session_id).revoked_at is not None
        notices = identity_deliveries(session, user_id, PASSWORD_CHANGED)
        new_notice = [notice for notice in notices if notice.id not in untouched_ids]
        assert len(new_notice) == 1
        assert new_notice[0].status is TransactionalEmailDeliveryStatus.PENDING
        assert email_jobs_for(session, [new_notice[0].id])[0].status is BackgroundJobStatus.PENDING

        for delivery_id in [*untouched_ids, foreign_id]:
            assert session.get(TransactionalEmailDelivery, delivery_id).status is (
                TransactionalEmailDeliveryStatus.PENDING
            )
        assert email_jobs_for(session, [foreign_id])[0].status is BackgroundJobStatus.PENDING


def test_canceled_reset_delivery_job_rerun_is_a_safe_noop(
    active_admin: tuple[int, int, str],
) -> None:
    """A stale or retried job for a canceled reset sends nothing and mints
    no token."""

    workspace_id, user_id, _email = active_admin
    seeded = seed_open_resets(workspace_id, user_id)

    with SessionFactory() as session:
        reset_administrator_password(
            session, raw_token=seeded.legacy_token, new_password=NEW_PASSWORD
        )
        session.commit()

    tokens_before = {token.id for token in _all_reset_tokens(user_id)}
    transport = FakeTransport([EmailAcceptance(provider_message_id="must-not-send")])
    handler = TransactionalEmailDeliveryHandler(
        session_factory=SessionFactory,
        settings=email_settings(),
        transport_factory=transport.factory,
    )

    for delivery_id in (seeded.pending_id, seeded.retry_id, seeded.sending_id):
        handler(snapshot_background_job(job_for(delivery_id)))

    assert transport.sent == []
    assert {token.id for token in _all_reset_tokens(user_id)} == tokens_before
    for delivery_id in (seeded.pending_id, seeded.retry_id, seeded.sending_id):
        assert load(TransactionalEmailDelivery, delivery_id).status is (
            TransactionalEmailDeliveryStatus.CANCELED
        )


def _all_reset_tokens(user_id: int) -> list[PasswordResetToken]:
    with SessionFactory() as session:
        return list(
            session.scalars(select(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        )


# Atomic rollback -----------------------------------------------------------


def _fail(*_args: object, **_kwargs: object):
    raise SQLAlchemyError("synthetic local cancellation failure")


@pytest.mark.parametrize("failure_point", ["delivery", "job"])
def test_cancellation_failure_rolls_back_the_entire_completion(
    client: TestClient,
    active_admin: tuple[int, int, str],
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    workspace_id, user_id, _email = active_admin
    seeded = seed_open_resets(workspace_id, user_id)
    auth_session_id = open_auth_session(user_id)
    unused_before = {token.id for token in unused_reset_tokens(user_id)}

    if failure_point == "delivery":
        monkeypatch.setattr(TransactionalEmailDeliveryRepository, "cancel_nonterminal", _fail)
    else:
        monkeypatch.setattr(BackgroundJobRepository, "cancel_before_execution", _fail)

    response = post(
        client,
        "/api/auth/password-reset/complete",
        {"token": seeded.legacy_token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert seeded.legacy_token not in response.text

    with SessionFactory() as session:
        assert verify_password(PASSWORD, session.get(User, user_id).password_hash)
        assert session.get(AuthSession, auth_session_id).revoked_at is None
        assert identity_deliveries(session, user_id, PASSWORD_CHANGED) == []

    assert {token.id for token in unused_reset_tokens(user_id)} == unused_before
    assert load(TransactionalEmailDelivery, seeded.pending_id).status is (
        TransactionalEmailDeliveryStatus.PENDING
    )
    assert load(TransactionalEmailDelivery, seeded.retry_id).status is (
        TransactionalEmailDeliveryStatus.RETRY_SCHEDULED
    )
    assert load(TransactionalEmailDelivery, seeded.sending_id).status is (
        TransactionalEmailDeliveryStatus.SENDING
    )
    assert job_for(seeded.pending_id).status is BackgroundJobStatus.PENDING
    assert job_for(seeded.retry_id).status is BackgroundJobStatus.RETRY_SCHEDULED


# Races between completion and the reset worker -----------------------------


def _pending_reset_with_legacy_token(workspace_id: int, user_id: int) -> tuple[str, int]:
    with SessionFactory() as session:
        legacy = issue_password_reset_token(
            session, user=session.get(User, user_id), lifetime_minutes=30
        )
        queued = _queue_reset(session, workspace_id, user_id)
        session.commit()
        return legacy.raw_token, queued.delivery.id


def _handler(transport: FakeTransport) -> TransactionalEmailDeliveryHandler:
    return TransactionalEmailDeliveryHandler(
        session_factory=SessionFactory,
        settings=email_settings(),
        transport_factory=transport.factory,
    )


def _complete(raw_token: str) -> None:
    with SessionFactory() as session:
        reset_administrator_password(session, raw_token=raw_token, new_password=NEW_PASSWORD)
        session.commit()


def _assert_token_unusable(raw_token: str) -> None:
    with SessionFactory() as session, pytest.raises(PasswordRecoveryError):
        reset_administrator_password(
            session, raw_token=raw_token, new_password="yet another synthetic passphrase"
        )


def test_worker_that_loses_race_after_token_creation_leaves_no_usable_token(
    active_admin: tuple[int, int, str],
) -> None:
    """Order forced by events: worker phase one commits a token, completion
    commits while the provider call is in flight, then the worker records an
    acceptance that is now stale."""

    workspace_id, user_id, _email = active_admin
    legacy, delivery_id = _pending_reset_with_legacy_token(workspace_id, user_id)
    snapshot = snapshot_background_job(job_for(delivery_id))
    in_flight = threading.Event()
    release = threading.Event()

    def provider(_message: OutgoingEmail) -> EmailAcceptance:
        in_flight.set()
        assert release.wait(timeout=10)
        return EmailAcceptance(provider_message_id="synthetic-late-accept")

    transport = FakeTransport([provider])
    errors: list[BaseException] = []

    def run_worker() -> None:
        try:
            _handler(transport)(snapshot)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    worker = threading.Thread(target=run_worker)
    worker.start()

    try:
        assert in_flight.wait(timeout=10)
        assert len(unused_reset_tokens(user_id)) == 2  # legacy + worker-created
        _complete(legacy)
    finally:
        release.set()
        worker.join(timeout=20)

    assert not worker.is_alive() and errors == []
    delivery = load(TransactionalEmailDelivery, delivery_id)
    assert delivery.status is TransactionalEmailDeliveryStatus.CANCELED, "stale outcome ignored"
    assert delivery.provider_message_id is None
    assert unused_reset_tokens(user_id) == []
    _assert_token_unusable(token_from(transport.sent[0]))


def test_worker_blocked_behind_completion_mints_no_token(
    active_admin: tuple[int, int, str],
) -> None:
    """Order forced by locks: completion holds the delivery lock uncommitted;
    the worker's phase one is observed waiting on PostgreSQL before
    completion commits."""

    workspace_id, user_id, _email = active_admin
    legacy, delivery_id = _pending_reset_with_legacy_token(workspace_id, user_id)
    snapshot = snapshot_background_job(job_for(delivery_id))
    transport = FakeTransport([EmailAcceptance(provider_message_id="must-not-send")])
    errors: list[BaseException] = []

    completion = SessionFactory()
    reset_administrator_password(completion, raw_token=legacy, new_password=NEW_PASSWORD)

    def run_worker() -> None:
        try:
            _handler(transport)(snapshot)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    worker = threading.Thread(target=run_worker)
    worker.start()

    try:
        deadline = time.monotonic() + 10
        with engine.connect() as observer:
            while True:
                waiting = observer.scalar(text("SELECT count(*) FROM pg_locks WHERE NOT granted"))
                observer.rollback()
                if waiting:
                    break
                assert time.monotonic() < deadline, "worker never blocked on the lock"
                time.sleep(0.02)
        completion.commit()
    finally:
        completion.close()
        worker.join(timeout=20)

    assert not worker.is_alive() and errors == []
    assert transport.sent == []
    assert load(TransactionalEmailDelivery, delivery_id).status is (
        TransactionalEmailDeliveryStatus.CANCELED
    )
    with SessionFactory() as session:
        linked = session.scalars(
            select(PasswordResetToken).where(
                PasswordResetToken.transactional_email_delivery_id == delivery_id
            )
        ).all()
    assert linked == []
    assert unused_reset_tokens(user_id) == []


@pytest.mark.parametrize("round_number", range(5))
def test_free_race_between_completion_and_worker_has_one_safe_outcome(
    active_admin: tuple[int, int, str],
    round_number: int,
) -> None:
    """Both start together at a barrier on separate connections. Either the
    reset delivery is canceled with no usable token, or it was accepted first
    and completion invalidated its token. Never a usable reset token."""

    del round_number
    workspace_id, user_id, _email = active_admin
    legacy, delivery_id = _pending_reset_with_legacy_token(workspace_id, user_id)
    snapshot = snapshot_background_job(job_for(delivery_id))
    transport = FakeTransport([EmailAcceptance(provider_message_id="synthetic-race")])
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(target) -> None:
        try:
            barrier.wait(timeout=10)
            target()
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(lambda: _handler(transport)(snapshot),)),
        threading.Thread(target=run, args=(lambda: _complete(legacy),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []

    with SessionFactory() as session:
        assert verify_password(NEW_PASSWORD, session.get(User, user_id).password_hash)

    delivery = load(TransactionalEmailDelivery, delivery_id)
    assert delivery.status in (
        TransactionalEmailDeliveryStatus.CANCELED,
        TransactionalEmailDeliveryStatus.SENT,
    )
    assert delivery.status not in NONTERMINAL
    assert unused_reset_tokens(user_id) == []

    for message in transport.sent:
        _assert_token_unusable(token_from(message))
