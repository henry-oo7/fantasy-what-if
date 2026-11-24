"""Transactional-email delivery worker (ADR 0009).

Uses synthetic recipients, keys, and provider responses. No test reaches a
network: the handler receives a scripted in-memory transport, and the
end-to-end cases use ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.security import hash_token
from app.db.auth_models import EmailVerificationToken, PasswordResetToken, User
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    InstallationStatus,
    OnboardingStage,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation, TransactionalEmailDelivery
from app.notifications.email_provider import (
    EmailAcceptance,
    EmailFailureCategory,
    EmailProviderError,
    OutgoingEmail,
)
from app.notifications.recipient_encryption import TransactionalEmailRecipientCipher
from app.notifications.resend_transport import ResendTransport
from app.repositories.account_token_repository import AccountTokenRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.services.background_job_handlers import build_background_job_handlers
from app.services.background_job_lease_recovery import recover_expired_background_jobs
from app.services.background_job_worker import (
    BackgroundJobDeferredError,
    BackgroundJobExecutionError,
    BackgroundJobSnapshot,
    execute_one_background_job,
    snapshot_background_job,
)
from app.services.transactional_email_delivery import TransactionalEmailDeliveryHandler
from app.services.transactional_email_intents import (
    queue_unscoped_completion_email,
    queue_workspace_email,
)
from app.services.transactional_email_job_outcome import apply_transactional_email_job_failure
from tests.support.email_delivery import (
    PUBLIC_BASE_URL,
    SYNTHETIC_API_KEY,
    FakeTransport,
    TrackedSession,
    email_settings,
    generated_keyring_json,
    isolated_factory,
    make_admin,
    make_workspace,
    token_from,
)

VERIFY = TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION
RESET = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET
CHANGED = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_CHANGED
STATUS = TransactionalEmailDeliveryStatus
EMAIL_JOB = BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY
COMPLETION_RECIPIENT = "deleted-owner@synthetic.invalid"
RAW_PROVIDER_BODY = "raw-provider-diagnostic-SENTINEL"


class FakeHeartbeat:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def raise_if_failed(self) -> None:
        pass


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    yield from isolated_factory()


@pytest.fixture
def keys() -> str:
    return generated_keyring_json()


def accepted(message_id: str = "provider-message-1") -> EmailAcceptance:
    return EmailAcceptance(provider_message_id=message_id)


def failure(category: EmailFailureCategory, retry_not_before=None) -> EmailProviderError:
    return EmailProviderError(category, retry_not_before=retry_not_before)


def handler_for(
    transport: FakeTransport,
    *,
    settings=None,
    factory: sessionmaker[Session],
    **kwargs,
) -> TransactionalEmailDeliveryHandler:
    return TransactionalEmailDeliveryHandler(
        session_factory=factory,
        settings=settings or email_settings(),
        transport_factory=transport.factory,
        **kwargs,
    )


def seed_identity(
    factory: sessionmaker[Session],
    event=VERIFY,
    *,
    verified: bool | None = None,
    maximum_attempts: int = 5,
) -> tuple[int, int, int, int, str]:
    """Create workspace, admin, delivery, and job; return their IDs and address."""

    with factory() as session:
        workspace = make_workspace(session, provisional=event is VERIFY)
        user = make_admin(
            session, workspace, verified=(event is not VERIFY) if verified is None else verified
        )
        queued = queue_workspace_email(
            session,
            workspace_id=workspace.id,
            user_id=user.id,
            event_type=event,
            idempotency_key=f"test:{event.value}:{user.id}",
        )
        queued.delivery.maximum_attempts = maximum_attempts
        assert queued.job is not None
        session.commit()
        return workspace.id, user.id, queued.delivery.id, queued.job.id, user.normalized_email


def seed_completion(factory: sessionmaker[Session], keys: str, *, maximum_attempts: int = 5):
    settings = email_settings(keys=keys)

    with factory() as session:
        queued = queue_unscoped_completion_email(
            session,
            recipient=COMPLETION_RECIPIENT,
            cipher=TransactionalEmailRecipientCipher(settings.transactional_email_keyring()),
            idempotency_key=f"test:completion:{datetime.now(UTC).timestamp()}",
        )
        queued.delivery.maximum_attempts = maximum_attempts
        assert queued.job is not None
        session.commit()
        return queued.delivery.id, queued.job.id, settings


def snapshot(
    factory: sessionmaker[Session],
    job_id: int,
    *,
    attempt_count: int = 1,
    workspace_id: object = "job",
) -> BackgroundJobSnapshot:
    with factory() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        base = snapshot_background_job(job)

    return BackgroundJobSnapshot(
        id=base.id,
        workspace_id=base.workspace_id if workspace_id == "job" else workspace_id,
        job_type=base.job_type,
        payload_version=base.payload_version,
        payload=base.payload,
        attempt_count=attempt_count,
        maximum_attempts=base.maximum_attempts,
    )


def load_delivery(factory: sessionmaker[Session], delivery_id: int) -> TransactionalEmailDelivery:
    with factory() as session:
        delivery = session.get(TransactionalEmailDelivery, delivery_id)
        assert delivery is not None
        return delivery


def tokens(factory: sessionmaker[Session], model, user_id: int) -> list:
    with factory() as session:
        return list(
            session.scalars(select(model).where(model.user_id == user_id).order_by(model.id))
        )


def run_worker(factory: sessionmaker[Session], handler, job_id: int):
    with factory() as session:
        session.get(BackgroundJob, job_id).priority = 10_000
        session.commit()

    return execute_one_background_job(
        session_factory=factory,
        worker_id="email-worker-01",
        lease_duration=timedelta(minutes=5),
        heartbeat_factory=FakeHeartbeat,
        handlers={EMAIL_JOB: handler},
    )


def load_job(factory: sessionmaker[Session], job_id: int) -> BackgroundJob | None:
    with factory() as session:
        return session.get(BackgroundJob, job_id)


def crash(_message: OutgoingEmail) -> EmailAcceptance:
    """Simulated process death after phase one committed."""

    raise KeyboardInterrupt


# Success, tokens, and transaction boundaries --------------------------------


def test_verification_is_sent_with_worker_created_hashed_token(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, address = seed_identity(factory)
    assert tokens(factory, EmailVerificationToken, user_id) == []
    transport = FakeTransport([accepted("msg-verify")])
    before = datetime.now(UTC)

    handler_for(transport, factory=factory)(snapshot(factory, job_id))

    [message] = transport.sent
    raw = token_from(message)
    [token] = tokens(factory, EmailVerificationToken, user_id)
    delivery = load_delivery(factory, delivery_id)

    assert message.recipient == address
    assert message.idempotency_key == f"{delivery_id}:1"
    assert f"{PUBLIC_BASE_URL}/verify-email?token={raw}" in message.text_body
    assert raw not in message.subject
    assert token.token_hash == hash_token(raw) and token.token_hash != raw
    assert (token.transactional_email_delivery_id, token.delivery_attempt) == (delivery_id, 1)
    assert token.used_at is None
    assert delivery.status is STATUS.SENT
    assert delivery.attempt_count == 1
    assert delivery.provider_message_id == "msg-verify"
    assert delivery.sent_at >= before
    assert delivery.provider_message_id_expires_at == delivery.sent_at + timedelta(days=30)
    assert delivery.retention_expires_at == delivery.sent_at + timedelta(days=90)
    assert delivery.safe_failure_code is None
    assert load_job(factory, job_id).payload == {"version": 1, "delivery_id": delivery_id}
    assert transport.closed == 1


def test_provider_call_happens_outside_any_transaction_after_commit(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory, RESET)
    observed: dict[str, object] = {}

    def inspect_during_send(message: OutgoingEmail) -> EmailAcceptance:
        observed["open_sessions"] = TrackedSession.open_count

        with factory() as session:
            delivery = session.get(TransactionalEmailDelivery, delivery_id)
            stored = session.scalars(
                select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
            ).all()
            observed["status"] = delivery.status
            observed["attempt"] = delivery.attempt_count
            observed["hashes"] = [token.token_hash for token in stored]

        observed["raw"] = token_from(message)
        return accepted()

    handler_for(FakeTransport([inspect_during_send]), factory=factory)(snapshot(factory, job_id))

    assert observed["open_sessions"] == 0
    assert observed["status"] is STATUS.SENDING
    assert observed["attempt"] == 1
    assert observed["hashes"] == [hash_token(observed["raw"])]


def test_password_changed_creates_no_token(factory: sessionmaker[Session]) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    transport = FakeTransport([accepted()])

    handler_for(transport, factory=factory)(snapshot(factory, job_id))

    assert "token=" not in transport.sent[0].text_body
    assert tokens(factory, EmailVerificationToken, user_id) == []
    assert tokens(factory, PasswordResetToken, user_id) == []
    assert load_delivery(factory, delivery_id).status is STATUS.SENT


def test_accepted_reset_supersedes_legacy_tokens_but_not_newer_delivery(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory, RESET)

    with factory() as session:
        user = session.get(User, user_id)
        newer = TransactionalEmailDelivery(
            scope=TransactionalEmailScope.WORKSPACE,
            workspace_id=user.workspace_id,
            user_id=user_id,
            event_type=RESET,
            idempotency_key=f"test:newer:{user_id}",
        )
        session.add(newer)
        session.flush()
        assert newer.id > delivery_id
        repository = AccountTokenRepository(session)
        legacy = repository.create_password_reset_token(
            user_id=user_id,
            raw_token="legacy-reset-token-synthetic-000000",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        newer_token = repository.create_delivery_password_reset_token(
            user_id=user_id,
            raw_token="newer-reset-token-synthetic-0000000",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            delivery_id=newer.id,
            delivery_attempt=1,
        )
        session.commit()
        legacy_id, newer_token_id = legacy.id, newer_token.id

    handler_for(FakeTransport([accepted()]), factory=factory)(snapshot(factory, job_id))

    stored = {token.id: token for token in tokens(factory, PasswordResetToken, user_id)}
    issued = [t for t in stored.values() if t.transactional_email_delivery_id == delivery_id]
    assert stored[legacy_id].used_at is not None
    assert stored[newer_token_id].used_at is None
    assert len(issued) == 1 and issued[0].used_at is None


# Retry, deferral, permanent failure -----------------------------------------


def test_uncertain_outcome_retries_with_new_attempt_and_new_token(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory)
    transport = FakeTransport(
        [failure(EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN), accepted("msg-2")]
    )
    handler = handler_for(transport, factory=factory)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler(snapshot(factory, job_id, attempt_count=1))

    assert caught.value.retryable is True
    assert caught.value.safe_error_code == "provider_timeout_unknown"
    delivery = load_delivery(factory, delivery_id)
    assert delivery.status is STATUS.RETRY_SCHEDULED
    assert delivery.safe_failure_code == "provider_timeout_unknown"
    assert delivery.sent_at is None and delivery.failed_at is None

    with factory() as session:
        session.get(TransactionalEmailDelivery, delivery_id).available_at = datetime.now(UTC)
        session.commit()

    handler(snapshot(factory, job_id, attempt_count=2))

    first, second = transport.sent
    assert (first.idempotency_key, second.idempotency_key) == (
        f"{delivery_id}:1",
        f"{delivery_id}:2",
    )
    assert token_from(first) != token_from(second)
    stored = tokens(factory, EmailVerificationToken, user_id)
    assert [(t.delivery_attempt, t.used_at) for t in stored] == [(1, None), (2, None)]
    assert load_delivery(factory, delivery_id).status is STATUS.SENT


@pytest.mark.parametrize(
    "category",
    [
        EmailFailureCategory.PROVIDER_UNAVAILABLE,
        EmailFailureCategory.PROVIDER_RESPONSE_INVALID,
    ],
)
def test_retryable_failures_schedule_retry(
    factory: sessionmaker[Session], category: EmailFailureCategory
) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler_for(FakeTransport([failure(category)]), factory=factory)(snapshot(factory, job_id))

    assert caught.value.retryable is True
    delivery = load_delivery(factory, delivery_id)
    assert delivery.status is STATUS.RETRY_SCHEDULED
    assert delivery.safe_failure_code == category.value


def test_rate_limit_defers_job_but_counts_delivery_attempt(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory)
    wait_until = datetime.now(UTC) + timedelta(seconds=30)
    transport = FakeTransport([failure(EmailFailureCategory.PROVIDER_RATE_LIMITED, wait_until)])

    with pytest.raises(BackgroundJobDeferredError) as caught:
        handler_for(transport, factory=factory)(snapshot(factory, job_id))

    delivery = load_delivery(factory, delivery_id)
    assert caught.value.safe_error_code == "provider_rate_limited"
    assert abs(caught.value.available_at - wait_until) < timedelta(seconds=1)
    assert delivery.status is STATUS.RETRY_SCHEDULED
    assert delivery.attempt_count == 1
    assert delivery.available_at == caught.value.available_at
    assert len(tokens(factory, EmailVerificationToken, user_id)) == 1


def test_rate_limit_deferral_is_clamped(factory: sessionmaker[Session]) -> None:
    _ws, _user, _delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    far = datetime.now(UTC) + timedelta(days=3)
    transport = FakeTransport([failure(EmailFailureCategory.PROVIDER_RATE_LIMITED, far)])

    with pytest.raises(BackgroundJobDeferredError) as caught:
        handler_for(transport, factory=factory, maximum_deferral=timedelta(minutes=5))(
            snapshot(factory, job_id)
        )

    assert caught.value.available_at <= datetime.now(UTC) + timedelta(minutes=5)


def test_worker_restores_job_attempt_on_rate_limit_deferral(
    factory: sessionmaker[Session],
) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    transport = FakeTransport([failure(EmailFailureCategory.PROVIDER_RATE_LIMITED)])

    result = run_worker(factory, handler_for(transport, factory=factory), job_id)

    job = load_job(factory, job_id)
    assert result.retry_scheduled is True
    assert job.status is BackgroundJobStatus.PENDING
    assert job.attempt_count == 0
    assert job.safe_error_code == "provider_rate_limited"
    assert load_delivery(factory, delivery_id).attempt_count == 1


def test_exhausted_rate_limit_attempts_fail_permanently(factory: sessionmaker[Session]) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory, maximum_attempts=2)
    transport = FakeTransport(
        [
            failure(EmailFailureCategory.PROVIDER_RATE_LIMITED),
            failure(EmailFailureCategory.PROVIDER_RATE_LIMITED),
        ]
    )
    handler = handler_for(transport, factory=factory)

    with pytest.raises(BackgroundJobDeferredError):
        handler(snapshot(factory, job_id))

    with factory() as session:
        session.get(TransactionalEmailDelivery, delivery_id).available_at = datetime.now(UTC)
        session.commit()

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler(snapshot(factory, job_id))

    delivery = load_delivery(factory, delivery_id)
    assert caught.value.retryable is False
    assert caught.value.safe_error_code == "provider_rate_limited"
    assert delivery.status is STATUS.PERMANENTLY_FAILED
    assert delivery.safe_failure_code == "provider_rate_limited"
    assert delivery.attempt_count == 2
    assert delivery.retention_expires_at == delivery.failed_at + timedelta(days=90)
    assert len(tokens(factory, EmailVerificationToken, user_id)) == 2


def test_daily_quota_defers_to_provider_boundary(factory: sessionmaker[Session]) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    boundary = datetime.now(UTC) + timedelta(hours=9)
    transport = FakeTransport([failure(EmailFailureCategory.PROVIDER_QUOTA_EXHAUSTED, boundary)])

    with pytest.raises(BackgroundJobDeferredError) as caught:
        handler_for(transport, factory=factory, maximum_deferral=timedelta(minutes=5))(
            snapshot(factory, job_id)
        )

    assert abs(caught.value.available_at - boundary) < timedelta(seconds=1)
    assert load_delivery(factory, delivery_id).safe_failure_code == "provider_quota_exhausted"


@pytest.mark.parametrize(
    "category",
    [
        EmailFailureCategory.RECIPIENT_INVALID,
        EmailFailureCategory.PROVIDER_AUTHENTICATION_FAILED,
        EmailFailureCategory.SENDER_NOT_VERIFIED,
        EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID,
    ],
)
def test_permanent_failures_finalize_delivery(
    factory: sessionmaker[Session], category: EmailFailureCategory
) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler_for(FakeTransport([failure(category)]), factory=factory)(snapshot(factory, job_id))

    delivery = load_delivery(factory, delivery_id)
    assert caught.value.retryable is False
    assert delivery.status is STATUS.PERMANENTLY_FAILED
    assert delivery.safe_failure_code == category.value
    assert delivery.failed_at is not None
    assert delivery.provider_message_id is None


def test_exhausted_job_attempts_fail_delivery_permanently(factory: sessionmaker[Session]) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler_for(
            FakeTransport([failure(EmailFailureCategory.PROVIDER_UNAVAILABLE)]), factory=factory
        )(snapshot(factory, job_id, attempt_count=5))

    assert caught.value.retryable is False
    assert load_delivery(factory, delivery_id).status is STATUS.PERMANENTLY_FAILED


# Unscoped completion and recipient scrubbing --------------------------------


def test_unscoped_completion_decrypts_sends_and_scrubs(
    factory: sessionmaker[Session], keys: str
) -> None:
    delivery_id, job_id, settings = seed_completion(factory, keys)
    transport = FakeTransport([accepted("msg-complete")])

    handler_for(transport, settings=settings, factory=factory)(snapshot(factory, job_id))

    delivery = load_delivery(factory, delivery_id)
    assert transport.sent[0].recipient == COMPLETION_RECIPIENT
    assert delivery.workspace_id is None
    assert delivery.status is STATUS.SENT
    assert delivery.encrypted_recipient is None
    assert delivery.recipient_key_version is None


def test_unscoped_retry_preserves_ciphertext_and_final_failure_scrubs(
    factory: sessionmaker[Session], keys: str
) -> None:
    delivery_id, job_id, settings = seed_completion(factory, keys)
    transport = FakeTransport(
        [
            failure(EmailFailureCategory.PROVIDER_UNAVAILABLE),
            failure(EmailFailureCategory.RECIPIENT_INVALID),
        ]
    )
    handler = handler_for(transport, settings=settings, factory=factory)

    with pytest.raises(BackgroundJobExecutionError):
        handler(snapshot(factory, job_id))

    retrying = load_delivery(factory, delivery_id)
    assert retrying.status is STATUS.RETRY_SCHEDULED
    assert retrying.encrypted_recipient is not None

    with factory() as session:
        session.get(TransactionalEmailDelivery, delivery_id).available_at = datetime.now(UTC)
        session.commit()

    with pytest.raises(BackgroundJobExecutionError):
        handler(snapshot(factory, job_id, attempt_count=2))

    failed = load_delivery(factory, delivery_id)
    assert failed.status is STATUS.PERMANENTLY_FAILED
    assert failed.encrypted_recipient is None and failed.recipient_key_version is None


def test_unscoped_completion_survives_workspace_deletion(
    factory: sessionmaker[Session], keys: str
) -> None:
    workspace_id, _user, scoped_delivery_id, _job, _address = seed_identity(factory, CHANGED)
    delivery_id, job_id, settings = seed_completion(factory, keys)

    with factory() as session:
        session.delete(session.get(Installation, workspace_id))
        session.commit()

    with factory() as session:
        assert session.get(TransactionalEmailDelivery, scoped_delivery_id) is None

    handler_for(FakeTransport([accepted()]), settings=settings, factory=factory)(
        snapshot(factory, job_id)
    )
    assert load_delivery(factory, delivery_id).status is STATUS.SENT


def test_missing_recipient_key_fails_safely(factory: sessionmaker[Session], keys: str) -> None:
    delivery_id, job_id, _settings = seed_completion(factory, keys)
    other_keys = email_settings(keys=generated_keyring_json())
    transport = FakeTransport()

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler_for(transport, settings=other_keys, factory=factory)(snapshot(factory, job_id))

    delivery = load_delivery(factory, delivery_id)
    assert caught.value.safe_error_code == "recipient_unavailable"
    assert transport.sent == []
    assert delivery.status is STATUS.PERMANENTLY_FAILED
    assert delivery.encrypted_recipient is None


# Ownership, payload, and eligibility ----------------------------------------


def test_payload_must_be_exact(factory: sessionmaker[Session]) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    base = snapshot(factory, job_id)
    transport = FakeTransport()

    for payload in (
        {"version": 1, "delivery_id": delivery_id, "recipient": "x@synthetic.invalid"},
        {"version": 2, "delivery_id": delivery_id},
        {"delivery_id": delivery_id},
    ):
        tampered = replace(base, payload=payload)

        with pytest.raises(BackgroundJobExecutionError) as caught:
            handler_for(transport, factory=factory)(tampered)

        assert caught.value.safe_error_code == "invalid_job_payload"

    assert transport.sent == []
    assert load_delivery(factory, delivery_id).status is STATUS.PENDING


def test_mismatched_workspace_fails_job_without_touching_delivery(
    factory: sessionmaker[Session], keys: str
) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    completion_id, completion_job_id, settings = seed_completion(factory, keys)

    with factory() as session:
        other_workspace = make_workspace(session).id
        session.commit()

    transport = FakeTransport()
    handler = handler_for(transport, settings=settings, factory=factory)

    for job_snapshot in (
        snapshot(factory, job_id, workspace_id=other_workspace),
        snapshot(factory, job_id, workspace_id=None),
        snapshot(factory, completion_job_id, workspace_id=other_workspace),
    ):
        with pytest.raises(BackgroundJobExecutionError) as caught:
            handler(job_snapshot)

        assert caught.value.safe_error_code == "delivery_ownership_mismatch"
        assert caught.value.retryable is False

    assert transport.sent == []
    assert load_delivery(factory, delivery_id).status is STATUS.PENDING
    assert load_delivery(factory, completion_id).encrypted_recipient is not None


def test_missing_scoped_delivery_after_cascade_is_safe_noop(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, _delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    job_snapshot = snapshot(factory, job_id)

    with factory() as session:
        session.delete(session.get(User, user_id))
        session.commit()

    transport = FakeTransport()
    handler_for(transport, factory=factory)(job_snapshot)

    assert transport.sent == []


def test_missing_unscoped_delivery_is_terminal(factory: sessionmaker[Session], keys: str) -> None:
    delivery_id, job_id, settings = seed_completion(factory, keys)
    job_snapshot = snapshot(factory, job_id)

    with factory() as session:
        session.delete(session.get(TransactionalEmailDelivery, delivery_id))
        session.commit()

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler_for(FakeTransport(), settings=settings, factory=factory)(job_snapshot)

    assert caught.value.safe_error_code == "delivery_missing"
    assert caught.value.retryable is False


def test_disabled_delivery_defers_without_attempt_or_token(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory)
    transport = FakeTransport()

    with pytest.raises(BackgroundJobDeferredError) as caught:
        handler_for(transport, settings=email_settings(enabled=False), factory=factory)(
            snapshot(factory, job_id)
        )

    delivery = load_delivery(factory, delivery_id)
    assert caught.value.safe_error_code == "delivery_disabled"
    assert delivery.status is STATUS.PENDING and delivery.attempt_count == 0
    assert tokens(factory, EmailVerificationToken, user_id) == []
    assert transport.sent == []


def test_not_yet_due_delivery_defers(factory: sessionmaker[Session]) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    due = datetime.now(UTC) + timedelta(days=2)

    with factory() as session:
        session.get(TransactionalEmailDelivery, delivery_id).available_at = due
        session.commit()

    with pytest.raises(BackgroundJobDeferredError) as caught:
        handler_for(FakeTransport(), factory=factory)(snapshot(factory, job_id))

    assert caught.value.available_at == due


@pytest.mark.parametrize(
    "event",
    [
        TransactionalEmailEventType.WORKSPACE_DELETION_REQUESTED,
        TransactionalEmailEventType.WORKSPACE_DELETION_REMINDER,
        TransactionalEmailEventType.WORKSPACE_DELETION_CANCELED,
    ],
)
def test_scoped_deletion_events_fail_safely_as_unsupported(
    factory: sessionmaker[Session], event: TransactionalEmailEventType
) -> None:
    with factory() as session:
        workspace = make_workspace(session)
        queued = queue_workspace_email(
            session,
            workspace_id=workspace.id,
            user_id=None,
            event_type=event,
            idempotency_key=f"test:{event.value}:{workspace.id}",
        )
        session.commit()
        delivery_id, job_id = queued.delivery.id, queued.job.id

    transport = FakeTransport()

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler_for(transport, factory=factory)(snapshot(factory, job_id))

    delivery = load_delivery(factory, delivery_id)
    assert caught.value.safe_error_code == "delivery_context_unsupported"
    assert caught.value.retryable is False
    assert delivery.status is STATUS.PERMANENTLY_FAILED
    assert delivery.attempt_count == 0
    assert transport.sent == []


def test_already_verified_administrator_cancels_verification(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory, VERIFY, verified=True)
    transport = FakeTransport()

    handler_for(transport, factory=factory)(snapshot(factory, job_id))

    delivery = load_delivery(factory, delivery_id)
    assert delivery.status is STATUS.CANCELED
    assert delivery.retention_expires_at == delivery.canceled_at + timedelta(days=90)
    assert tokens(factory, EmailVerificationToken, user_id) == []
    assert transport.sent == []


def set_workspace_lifecycle(
    factory: sessionmaker[Session],
    workspace_id: int,
    *,
    registration_status: WorkspaceRegistrationStatus | None = None,
    onboarding_stage: OnboardingStage | None = None,
    installation_status: InstallationStatus | None = None,
) -> None:
    with factory() as session:
        workspace = session.get(Installation, workspace_id)
        assert workspace is not None

        if registration_status is not None:
            workspace.registration_status = registration_status

            if registration_status is WorkspaceRegistrationStatus.DELETION_PENDING:
                requested_at = datetime.now(UTC)
                workspace.deletion_requested_at = requested_at
                workspace.deletion_scheduled_for = requested_at + timedelta(days=7)

        if onboarding_stage is not None:
            workspace.onboarding_stage = onboarding_stage

        if installation_status is not None:
            workspace.status = installation_status

        session.commit()


@pytest.mark.parametrize(
    ("registration_status", "onboarding_stage"),
    [
        pytest.param(
            WorkspaceRegistrationStatus.ACTIVE,
            OnboardingStage.EMAIL_VERIFICATION_PENDING,
            id="workspace-active",
        ),
        pytest.param(
            WorkspaceRegistrationStatus.PROVISIONAL,
            OnboardingStage.PROVIDER_SETUP_PENDING,
            id="stage-past-verification",
        ),
        pytest.param(
            WorkspaceRegistrationStatus.PROVISIONAL,
            OnboardingStage.REGISTRATION_PENDING,
            id="stage-before-verification",
        ),
        pytest.param(
            WorkspaceRegistrationStatus.SUSPENDED,
            OnboardingStage.EMAIL_VERIFICATION_PENDING,
            id="workspace-suspended",
        ),
    ],
)
def test_verification_is_canceled_when_workspace_is_not_pending_verification(
    factory: sessionmaker[Session],
    registration_status: WorkspaceRegistrationStatus,
    onboarding_stage: OnboardingStage,
) -> None:
    workspace_id, user_id, delivery_id, job_id, _address = seed_identity(factory, VERIFY)
    set_workspace_lifecycle(
        factory,
        workspace_id,
        registration_status=registration_status,
        onboarding_stage=onboarding_stage,
    )
    transport = FakeTransport()

    result = run_worker(factory, handler_for(transport, factory=factory), job_id)

    delivery = load_delivery(factory, delivery_id)
    assert result.succeeded is True
    assert delivery.status is STATUS.CANCELED
    assert delivery.attempt_count == 0
    assert tokens(factory, EmailVerificationToken, user_id) == []
    assert transport.sent == []


@pytest.mark.parametrize(
    ("registration_status", "installation_status"),
    [
        pytest.param(
            WorkspaceRegistrationStatus.SUSPENDED,
            InstallationStatus.ACTIVE,
            id="registration-suspended",
        ),
        pytest.param(
            WorkspaceRegistrationStatus.DELETED,
            InstallationStatus.ACTIVE,
            id="registration-deleted",
        ),
        pytest.param(
            WorkspaceRegistrationStatus.ACTIVE,
            InstallationStatus.SETUP_IN_PROGRESS,
            id="installation-not-active",
        ),
    ],
)
def test_reset_is_canceled_when_workspace_fails_every_lifecycle_predicate(
    factory: sessionmaker[Session],
    registration_status: WorkspaceRegistrationStatus,
    installation_status: InstallationStatus,
) -> None:
    workspace_id, user_id, delivery_id, job_id, _address = seed_identity(factory, RESET)
    set_workspace_lifecycle(
        factory,
        workspace_id,
        registration_status=registration_status,
        installation_status=installation_status,
    )
    transport = FakeTransport()

    result = run_worker(factory, handler_for(transport, factory=factory), job_id)

    delivery = load_delivery(factory, delivery_id)
    assert result.succeeded is True
    assert delivery.status is STATUS.CANCELED
    assert delivery.attempt_count == 0
    assert tokens(factory, PasswordResetToken, user_id) == []
    assert transport.sent == []


def test_reset_is_canceled_for_unverified_administrator(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory, RESET, verified=False)
    transport = FakeTransport()

    handler_for(transport, factory=factory)(snapshot(factory, job_id))

    assert load_delivery(factory, delivery_id).status is STATUS.CANCELED
    assert tokens(factory, PasswordResetToken, user_id) == []
    assert transport.sent == []


@pytest.mark.parametrize(
    ("registration_status", "onboarding_stage", "installation_status"),
    [
        pytest.param(
            WorkspaceRegistrationStatus.DELETION_PENDING,
            OnboardingStage.COMPLETE,
            InstallationStatus.ACTIVE,
            id="deletion-pending",
        ),
        pytest.param(
            WorkspaceRegistrationStatus.PROVISIONAL,
            OnboardingStage.PROVIDER_SETUP_PENDING,
            InstallationStatus.SETUP_IN_PROGRESS,
            id="resumable-onboarding",
        ),
    ],
)
def test_reset_is_sent_for_deletion_pending_and_resumable_onboarding(
    factory: sessionmaker[Session],
    registration_status: WorkspaceRegistrationStatus,
    onboarding_stage: OnboardingStage,
    installation_status: InstallationStatus,
) -> None:
    workspace_id, user_id, delivery_id, job_id, address = seed_identity(factory, RESET)
    set_workspace_lifecycle(
        factory,
        workspace_id,
        registration_status=registration_status,
        onboarding_stage=onboarding_stage,
        installation_status=installation_status,
    )
    transport = FakeTransport([accepted("msg-reset")])

    handler_for(transport, factory=factory)(snapshot(factory, job_id))

    [message] = transport.sent
    [token] = tokens(factory, PasswordResetToken, user_id)
    assert message.recipient == address
    assert token.token_hash == hash_token(token_from(message))
    assert load_delivery(factory, delivery_id).status is STATUS.SENT


def test_password_changed_is_sent_to_active_administrator_without_tokens(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, address = seed_identity(factory, CHANGED)
    transport = FakeTransport([accepted("msg-changed")])

    result = run_worker(factory, handler_for(transport, factory=factory), job_id)

    [message] = transport.sent
    assert result.succeeded is True
    assert message.recipient == address
    assert "token=" not in message.text_body
    assert tokens(factory, EmailVerificationToken, user_id) == []
    assert tokens(factory, PasswordResetToken, user_id) == []
    assert load_delivery(factory, delivery_id).status is STATUS.SENT


@pytest.mark.parametrize("event", [VERIFY, RESET, CHANGED])
def test_identity_delivery_removed_with_user_is_safe_noop(
    factory: sessionmaker[Session], event: TransactionalEmailEventType
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory, event)
    job_snapshot = snapshot(factory, job_id)

    with factory() as session:
        session.delete(session.get(User, user_id))
        session.commit()

    transport = FakeTransport()
    handler_for(transport, factory=factory)(job_snapshot)

    assert transport.sent == []

    with factory() as session:
        assert session.get(TransactionalEmailDelivery, delivery_id) is None


def test_terminal_delivery_is_idempotent_noop(factory: sessionmaker[Session]) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    handler_for(FakeTransport([accepted()]), factory=factory)(snapshot(factory, job_id))
    transport = FakeTransport()

    handler_for(transport, factory=factory)(snapshot(factory, job_id))

    assert transport.sent == []
    assert load_delivery(factory, delivery_id).status is STATUS.SENT


# Restart, lease recovery, stale attempts, and races -------------------------


def test_crash_after_token_commit_before_http_retries_with_new_attempt(
    factory: sessionmaker[Session],
) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory)

    with pytest.raises(KeyboardInterrupt):
        handler_for(FakeTransport([crash]), factory=factory)(snapshot(factory, job_id))

    crashed = load_delivery(factory, delivery_id)
    assert crashed.status is STATUS.SENDING and crashed.attempt_count == 1
    [orphan] = tokens(factory, EmailVerificationToken, user_id)

    transport = FakeTransport([accepted("after-restart")])
    handler_for(transport, factory=factory)(snapshot(factory, job_id, attempt_count=2))

    stored = tokens(factory, EmailVerificationToken, user_id)
    delivery = load_delivery(factory, delivery_id)
    assert transport.sent[0].idempotency_key == f"{delivery_id}:2"
    assert [token.delivery_attempt for token in stored] == [1, 2]
    assert stored[0].id == orphan.id and stored[0].used_at is None
    assert delivery.status is STATUS.SENT and delivery.attempt_count == 2


def test_restart_from_sending_with_no_attempts_left_fails_as_unknown(
    factory: sessionmaker[Session],
) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED, maximum_attempts=1)

    with pytest.raises(KeyboardInterrupt):
        handler_for(FakeTransport([crash]), factory=factory)(snapshot(factory, job_id))

    transport = FakeTransport()

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler_for(transport, factory=factory)(snapshot(factory, job_id, attempt_count=2))

    delivery = load_delivery(factory, delivery_id)
    assert caught.value.safe_error_code == "provider_timeout_unknown"
    assert transport.sent == []
    assert delivery.status is STATUS.PERMANENTLY_FAILED


def test_accepted_but_local_commit_lost_resends_as_new_attempt(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    transport = FakeTransport([accepted("first-accepted"), accepted("second-accepted")])
    handler = handler_for(transport, factory=factory)
    original = handler._record_outcome

    def lose_commit(*_args, **_kwargs):
        raise OperationalError("UPDATE", {}, Exception("connection lost"))

    monkeypatch.setattr(handler, "_record_outcome", lose_commit)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler(snapshot(factory, job_id))

    assert caught.value.retryable is True
    assert caught.value.safe_error_code == "delivery_state_unavailable"
    assert load_delivery(factory, delivery_id).status is STATUS.SENDING

    monkeypatch.setattr(handler, "_record_outcome", original)
    handler(snapshot(factory, job_id, attempt_count=2))

    delivery = load_delivery(factory, delivery_id)
    assert [m.idempotency_key for m in transport.sent] == [
        f"{delivery_id}:1",
        f"{delivery_id}:2",
    ]
    assert delivery.status is STATUS.SENT
    assert delivery.provider_message_id == "second-accepted"


def test_stale_worker_outcome_never_overwrites_newer_attempt(
    factory: sessionmaker[Session],
) -> None:
    """Worker A loses its lease mid-HTTP; worker B takes over and finishes."""

    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory)
    worker_b = handler_for(FakeTransport([accepted("worker-b")]), factory=factory)

    def lease_lost_during_send(_message: OutgoingEmail) -> EmailAcceptance:
        worker_b(snapshot(factory, job_id, attempt_count=2))
        return accepted("worker-a-stale")

    handler_for(FakeTransport([lease_lost_during_send]), factory=factory)(snapshot(factory, job_id))

    delivery = load_delivery(factory, delivery_id)
    assert delivery.status is STATUS.SENT
    assert delivery.provider_message_id == "worker-b"
    assert delivery.attempt_count == 2
    assert [t.used_at for t in tokens(factory, EmailVerificationToken, user_id)] == [None, None]


def test_stale_failure_after_newer_attempt_is_ignored(factory: sessionmaker[Session]) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)
    worker_b = handler_for(FakeTransport([accepted("worker-b")]), factory=factory)

    def lease_lost(_message: OutgoingEmail) -> EmailAcceptance:
        worker_b(snapshot(factory, job_id, attempt_count=2))
        raise failure(EmailFailureCategory.RECIPIENT_INVALID)

    handler_for(FakeTransport([lease_lost]), factory=factory)(snapshot(factory, job_id))

    assert load_delivery(factory, delivery_id).status is STATUS.SENT


def test_cancellation_during_http_is_never_reverted(
    factory: sessionmaker[Session], keys: str
) -> None:
    delivery_id, job_id, settings = seed_completion(factory, keys)

    def canceled_by_another_actor(_message: OutgoingEmail) -> EmailAcceptance:
        with factory() as session:
            delivery = session.get(TransactionalEmailDelivery, delivery_id)
            now = datetime.now(UTC)
            delivery.status = STATUS.CANCELED
            delivery.canceled_at = now
            delivery.retention_expires_at = now + timedelta(days=90)
            delivery.encrypted_recipient = None
            delivery.recipient_key_version = None
            session.commit()
        return accepted("too-late")

    handler_for(FakeTransport([canceled_by_another_actor]), settings=settings, factory=factory)(
        snapshot(factory, job_id)
    )

    delivery = load_delivery(factory, delivery_id)
    assert delivery.status is STATUS.CANCELED
    assert delivery.provider_message_id is None
    assert delivery.encrypted_recipient is None


def test_workspace_cascade_during_http_resolves_job_safely(
    factory: sessionmaker[Session],
) -> None:
    workspace_id, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)

    def workspace_deleted(_message: OutgoingEmail) -> EmailAcceptance:
        with factory() as session:
            session.delete(session.get(Installation, workspace_id))
            session.commit()
        return accepted()

    result = run_worker(
        factory, handler_for(FakeTransport([workspace_deleted]), factory=factory), job_id
    )

    assert result.succeeded is True
    assert load_job(factory, job_id) is None
    with factory() as session:
        assert session.get(TransactionalEmailDelivery, delivery_id) is None


def test_lease_recovery_then_retry_creates_new_attempt(factory: sessionmaker[Session]) -> None:
    _ws, user_id, delivery_id, job_id, _address = seed_identity(factory)

    with factory() as session:
        session.get(BackgroundJob, job_id).priority = 10_000
        session.commit()

    with factory() as session:
        job = BackgroundJobRepository(session).claim_next_available(
            worker_id="dead-worker", lease_duration=timedelta(seconds=1)
        )
        assert job.id == job_id
        session.commit()

    with pytest.raises(KeyboardInterrupt):
        handler_for(FakeTransport([crash]), factory=factory)(snapshot(factory, job_id))

    with factory() as session:
        recovered = recover_expired_background_jobs(
            session, now=datetime.now(UTC) + timedelta(minutes=1), retry_delay=timedelta(0)
        )
        session.get(BackgroundJob, job_id).available_at = datetime.now(UTC)
        session.commit()

    assert recovered.retry_scheduled_count >= 1
    assert load_delivery(factory, delivery_id).status is STATUS.SENDING

    transport = FakeTransport([accepted("recovered")])
    result = run_worker(factory, handler_for(transport, factory=factory), job_id)

    assert result.succeeded is True
    assert transport.sent[0].idempotency_key == f"{delivery_id}:2"
    assert load_delivery(factory, delivery_id).status is STATUS.SENT
    assert len(tokens(factory, EmailVerificationToken, user_id)) == 2


def test_lease_recovery_exhaustion_finalizes_sending_delivery(
    factory: sessionmaker[Session],
) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)

    with factory() as session:
        job = session.get(BackgroundJob, job_id)
        job.priority = 10_000
        job.maximum_attempts = 1
        session.commit()

    with factory() as session:
        BackgroundJobRepository(session).claim_next_available(
            worker_id="dead-worker", lease_duration=timedelta(seconds=1)
        )
        session.commit()

    with pytest.raises(KeyboardInterrupt):
        handler_for(FakeTransport([crash]), factory=factory)(snapshot(factory, job_id))

    with factory() as session:
        recover_expired_background_jobs(session, now=datetime.now(UTC) + timedelta(minutes=1))
        session.commit()

    delivery = load_delivery(factory, delivery_id)
    assert load_job(factory, job_id).status is BackgroundJobStatus.FAILED
    assert delivery.status is STATUS.PERMANENTLY_FAILED
    assert delivery.safe_failure_code == "provider_timeout_unknown"


def test_job_failure_hook_respects_ownership(factory: sessionmaker[Session]) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)

    with factory() as session:
        other = make_workspace(session)
        job = session.get(BackgroundJob, job_id)
        job.workspace_id = other.id
        apply_transactional_email_job_failure(session, job=job)
        session.flush()
        assert session.get(TransactionalEmailDelivery, delivery_id).status is STATUS.PENDING
        session.rollback()

    with factory() as session:
        apply_transactional_email_job_failure(session, job=session.get(BackgroundJob, job_id))
        session.commit()

    delivery = load_delivery(factory, delivery_id)
    assert delivery.status is STATUS.PERMANENTLY_FAILED
    assert delivery.safe_failure_code == "delivery_job_failed"


def test_unexpected_handler_failure_never_leaves_delivery_nonterminal(
    factory: sessionmaker[Session],
) -> None:
    _ws, _user, delivery_id, job_id, _address = seed_identity(factory, CHANGED)

    def explode(_job: BackgroundJobSnapshot) -> None:
        raise RuntimeError("synthetic unexpected failure")

    result = run_worker(factory, explode, job_id)

    assert result.failed is True
    assert load_delivery(factory, delivery_id).status is STATUS.PERMANENTLY_FAILED


# Worker integration and registration ----------------------------------------


def test_handler_is_registered_for_email_jobs(factory: sessionmaker[Session]) -> None:
    handlers = build_background_job_handlers(session_factory=factory, settings=email_settings())

    assert isinstance(handlers[EMAIL_JOB], TransactionalEmailDeliveryHandler)


def resend_factory(responder):
    def build(settings) -> ResendTransport:
        return ResendTransport(
            api_key=settings.transactional_email_api_key,
            transport=httpx.MockTransport(responder),
        )

    return build


def test_end_to_end_worker_with_mocked_resend_http(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    _ws, user_id, delivery_id, job_id, address = seed_identity(factory, RESET)
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "resend-message-77"})

    handler = TransactionalEmailDeliveryHandler(
        session_factory=factory,
        settings=email_settings(),
        transport_factory=resend_factory(responder),
    )
    result = run_worker(factory, handler, job_id)

    [request] = requests
    body = json.loads(request.content)
    raw = body["text"].split("?token=")[1].split()[0]
    job = load_job(factory, job_id)
    delivery = load_delivery(factory, delivery_id)

    assert result.succeeded is True
    assert job.status is BackgroundJobStatus.SUCCEEDED
    assert request.headers["idempotency-key"] == f"{delivery_id}:1"
    assert delivery.provider_message_id == "resend-message-77"
    assert_no_secrets(caplog.text, job, delivery, raw, address)


@pytest.mark.parametrize(
    ("status", "body", "headers", "job_status", "attempts"),
    [
        (503, {"name": "service_unavailable"}, {}, BackgroundJobStatus.RETRY_SCHEDULED, 1),
        (
            429,
            {"name": "rate_limit_exceeded"},
            {"retry-after": "5"},
            BackgroundJobStatus.PENDING,
            0,
        ),
        (422, {"name": "missing_required_field"}, {}, BackgroundJobStatus.FAILED, 1),
    ],
)
def test_worker_outcomes_with_mocked_resend_http(
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    status: int,
    body: dict,
    headers: dict,
    job_status: BackgroundJobStatus,
    attempts: int,
) -> None:
    caplog.set_level(logging.DEBUG)
    _ws, _user, delivery_id, job_id, address = seed_identity(factory)
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["text"].split("?token=")[1].split()[0])
        return httpx.Response(
            status, json={**body, "message": f"{RAW_PROVIDER_BODY} {address}"}, headers=headers
        )

    handler = TransactionalEmailDeliveryHandler(
        session_factory=factory,
        settings=email_settings(),
        transport_factory=resend_factory(responder),
    )
    run_worker(factory, handler, job_id)

    job = load_job(factory, job_id)
    delivery = load_delivery(factory, delivery_id)
    assert job.status is job_status
    assert job.attempt_count == attempts
    assert delivery.attempt_count == 1
    assert_no_secrets(caplog.text, job, delivery, seen[0], address)


def assert_no_secrets(
    log_text: str,
    job: BackgroundJob,
    delivery: TransactionalEmailDelivery,
    raw_token: str,
    recipient: str,
) -> None:
    job_text = json.dumps(
        {
            "payload": job.payload,
            "code": job.safe_error_code,
            "message": job.safe_error_message,
            "progress": job.progress,
        }
    )
    delivery_text = json.dumps(
        {
            column.name: str(getattr(delivery, column.key))
            for column in TransactionalEmailDelivery.__table__.columns
            if hasattr(delivery, column.key)
        }
    )

    for secret in (raw_token, recipient, SYNTHETIC_API_KEY, RAW_PROVIDER_BODY):
        assert secret not in log_text
        assert secret not in job_text
        assert secret not in delivery_text

    assert set(job.payload) == {"version", "delivery_id"}


def test_errors_and_snapshots_carry_no_secrets(factory: sessionmaker[Session], keys: str) -> None:
    delivery_id, job_id, settings = seed_completion(factory, keys)

    with factory() as session:
        ciphertext = session.get(TransactionalEmailDelivery, delivery_id).encrypted_recipient

    job_snapshot = snapshot(factory, job_id)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler_for(
            FakeTransport([failure(EmailFailureCategory.RECIPIENT_INVALID)]),
            settings=settings,
            factory=factory,
        )(job_snapshot)

    rendered = f"{caught.value!s} {caught.value.safe_error_message} {job_snapshot!r}"

    for secret in (COMPLETION_RECIPIENT, ciphertext, SYNTHETIC_API_KEY):
        assert secret not in rendered
