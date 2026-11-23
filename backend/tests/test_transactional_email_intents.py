"""Repository and service tests: idempotent, atomic delivery-plus-job creation."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
)
from app.db.platform_models import BackgroundJob, TransactionalEmailDelivery
from app.notifications.delivery_job_contract import transactional_email_job_idempotency_key
from app.repositories.transactional_email_delivery_repository import (
    SAFE_STATE_RETENTION,
    DeliveryIdempotencyConflictError,
    DeliveryStateError,
    TransactionalEmailDeliveryRepository,
)
from app.services.transactional_email_intents import (
    cancel_queued_email,
    queue_unscoped_completion_email,
    queue_workspace_email,
)
from tests.support.transactional_email import (
    NOW,
    RECIPIENT,
    database_session_fixture,
    make_cipher,
    make_user,
    make_workspace,
)

Event = TransactionalEmailEventType
Status = TransactionalEmailDeliveryStatus


@pytest.fixture
def database_session() -> Iterator[Session]:
    yield from database_session_fixture()


@pytest.fixture
def workspace_and_user(database_session: Session):
    workspace = make_workspace(database_session)
    return workspace, make_user(database_session, workspace)


def count(session: Session, model: type, *where: object) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def test_queue_creates_delivery_and_job_together(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user

    queued = queue_workspace_email(
        database_session,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        idempotency_key="verify:1",
    )

    assert queued.existing is False
    assert queued.delivery.scope is TransactionalEmailScope.WORKSPACE
    assert queued.job is not None
    assert queued.job.job_type is BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY
    assert queued.job.workspace_id == workspace.id
    assert queued.job.payload == {"version": 1, "delivery_id": queued.delivery.id}
    assert queued.job.idempotency_key == transactional_email_job_idempotency_key(queued.delivery.id)


def test_repeat_call_returns_existing_delivery_and_job(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user
    kwargs = {
        "workspace_id": workspace.id,
        "user_id": user.id,
        "event_type": Event.ADMINISTRATOR_PASSWORD_RESET,
        "idempotency_key": "reset:1",
    }

    first = queue_workspace_email(database_session, **kwargs)
    second = queue_workspace_email(database_session, **kwargs)

    assert second.existing is True
    assert second.delivery.id == first.delivery.id
    assert second.job is not None and first.job is not None
    assert second.job.id == first.job.id
    assert count(database_session, TransactionalEmailDelivery) >= 1
    assert (
        count(
            database_session,
            BackgroundJob,
            BackgroundJob.idempotency_key
            == transactional_email_job_idempotency_key(first.delivery.id),
        )
        == 1
    )


@pytest.mark.parametrize(
    "override",
    [
        {"event_type": Event.ADMINISTRATOR_PASSWORD_CHANGED},
        {"user_id": "other"},
        {"workspace_id": "other"},
    ],
)
def test_incompatible_key_reuse_is_rejected(
    database_session: Session, workspace_and_user, override: dict
) -> None:
    workspace, user = workspace_and_user
    other_workspace = make_workspace(database_session)
    other_user = make_user(database_session, other_workspace)
    base = {
        "workspace_id": workspace.id,
        "user_id": user.id,
        "event_type": Event.ADMINISTRATOR_PASSWORD_RESET,
        "idempotency_key": "reset:conflict",
    }
    queue_workspace_email(database_session, **base)

    changed = dict(base)
    for field, value in override.items():
        changed[field] = {
            ("user_id", "other"): other_user.id,
            ("workspace_id", "other"): other_workspace.id,
        }.get((field, value), value)

    with pytest.raises(DeliveryIdempotencyConflictError):
        queue_workspace_email(database_session, **changed)


def test_scope_reuse_conflicts_both_directions(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user
    cipher = make_cipher()

    queue_workspace_email(
        database_session,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.WORKSPACE_DELETION_REQUESTED,
        idempotency_key="shared-key",
    )
    with pytest.raises(DeliveryIdempotencyConflictError):
        queue_unscoped_completion_email(
            database_session, recipient=RECIPIENT, cipher=cipher, idempotency_key="shared-key"
        )

    queue_unscoped_completion_email(
        database_session, recipient=RECIPIENT, cipher=cipher, idempotency_key="done-key"
    )
    with pytest.raises(DeliveryIdempotencyConflictError):
        queue_workspace_email(
            database_session,
            workspace_id=workspace.id,
            user_id=None,
            event_type=Event.WORKSPACE_DELETION_REQUESTED,
            idempotency_key="done-key",
        )


def test_validation_of_scoped_creation_arguments(
    database_session: Session, workspace_and_user
) -> None:
    workspace, _ = workspace_and_user

    with pytest.raises(ValueError, match="require user_id"):
        queue_workspace_email(
            database_session,
            workspace_id=workspace.id,
            user_id=None,
            event_type=Event.ADMINISTRATOR_EMAIL_VERIFICATION,
            idempotency_key="k",
        )

    with pytest.raises(ValueError, match="unscoped"):
        queue_workspace_email(
            database_session,
            workspace_id=workspace.id,
            user_id=None,
            event_type=Event.WORKSPACE_DELETION_COMPLETED,
            idempotency_key="k",
        )

    with pytest.raises(ValueError):
        queue_workspace_email(
            database_session,
            workspace_id=workspace.id,
            user_id=None,
            event_type=Event.WORKSPACE_DELETION_REMINDER,
            idempotency_key="   ",
        )


def test_unscoped_completion_has_encrypted_recipient_and_null_workspace_job(
    database_session: Session,
) -> None:
    cipher = make_cipher(version=3)

    queued = queue_unscoped_completion_email(
        database_session, recipient=RECIPIENT, cipher=cipher, idempotency_key="completed:1"
    )
    delivery = queued.delivery

    assert delivery.scope is TransactionalEmailScope.UNSCOPED
    assert delivery.workspace_id is None and delivery.user_id is None
    assert delivery.recipient_key_version == 3
    assert delivery.encrypted_recipient and RECIPIENT not in delivery.encrypted_recipient
    assert (
        cipher.decrypt(
            ciphertext=delivery.encrypted_recipient,
            key_version=3,
            delivery_id=delivery.id,
        )
        == RECIPIENT
    )
    assert queued.job is not None and queued.job.workspace_id is None


def test_unscoped_repeat_does_not_reencrypt_or_duplicate(database_session: Session) -> None:
    cipher = make_cipher()
    first = queue_unscoped_completion_email(
        database_session, recipient=RECIPIENT, cipher=cipher, idempotency_key="completed:2"
    )
    ciphertext = first.delivery.encrypted_recipient

    second = queue_unscoped_completion_email(
        database_session, recipient=RECIPIENT, cipher=cipher, idempotency_key="completed:2"
    )

    assert second.existing is True
    assert second.delivery.id == first.delivery.id
    assert second.delivery.encrypted_recipient == ciphertext
    assert second.job is not None and second.job.id == first.job.id


def test_rollback_leaves_neither_delivery_nor_job(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user
    key = "rollback:1"

    savepoint = database_session.begin_nested()
    queue_workspace_email(
        database_session,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        idempotency_key=key,
    )
    savepoint.rollback()

    assert (
        count(
            database_session,
            TransactionalEmailDelivery,
            TransactionalEmailDelivery.idempotency_key == key,
        )
        == 0
    )
    assert (
        count(
            database_session,
            BackgroundJob,
            BackgroundJob.job_type == BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY,
            BackgroundJob.workspace_id == workspace.id,
        )
        == 0
    )


def test_commit_flag_commits_both_rows(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user

    queued = queue_workspace_email(
        database_session,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.ADMINISTRATOR_PASSWORD_CHANGED,
        idempotency_key="commit:1",
        commit=True,
    )

    assert queued.job is not None
    assert database_session.get(TransactionalEmailDelivery, queued.delivery.id) is not None
    assert database_session.get(BackgroundJob, queued.job.id) is not None


def test_future_available_at_is_shared_by_delivery_and_job(
    database_session: Session, workspace_and_user
) -> None:
    workspace, _ = workspace_and_user
    when = NOW + timedelta(days=30)

    queued = queue_workspace_email(
        database_session,
        workspace_id=workspace.id,
        user_id=None,
        event_type=Event.WORKSPACE_DELETION_REMINDER,
        idempotency_key="reminder:1",
        available_at=when,
    )

    assert queued.delivery.available_at == when
    assert queued.job is not None and queued.job.available_at == when


def test_cross_workspace_isolation(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user
    other = make_workspace(database_session)
    other_user = make_user(database_session, other)

    mine = queue_workspace_email(
        database_session,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        idempotency_key="iso:mine",
    )
    theirs = queue_workspace_email(
        database_session,
        workspace_id=other.id,
        user_id=other_user.id,
        event_type=Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        idempotency_key="iso:theirs",
    )

    assert mine.delivery.id != theirs.delivery.id
    assert mine.job is not None and theirs.job is not None
    assert mine.job.workspace_id == workspace.id
    assert theirs.job.workspace_id == other.id

    mine_id, theirs_id = mine.delivery.id, theirs.delivery.id
    database_session.delete(other_user)
    database_session.delete(other)
    database_session.flush()
    database_session.expire_all()

    assert database_session.get(TransactionalEmailDelivery, mine_id) is not None
    assert database_session.get(TransactionalEmailDelivery, theirs_id) is None


def test_cancel_pending_cancels_delivery_and_queued_job(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user
    queued = queue_workspace_email(
        database_session,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        idempotency_key="cancel:1",
    )

    assert cancel_queued_email(database_session, delivery_id=queued.delivery.id, now=NOW) is True
    assert cancel_queued_email(database_session, delivery_id=queued.delivery.id, now=NOW) is False

    database_session.refresh(queued.delivery)
    database_session.refresh(queued.job)
    assert queued.delivery.status is Status.CANCELED
    assert queued.delivery.canceled_at == NOW
    assert queued.delivery.retention_expires_at == NOW + SAFE_STATE_RETENTION
    assert queued.job.status is BackgroundJobStatus.CANCELED

    # A canceled delivery is final: a retry returns it without queueing a job.
    again = queue_workspace_email(
        database_session,
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        idempotency_key="cancel:1",
    )
    assert again.existing is True and again.job is None


def test_cancel_scrubs_unscoped_recipient(database_session: Session) -> None:
    queued = queue_unscoped_completion_email(
        database_session, recipient=RECIPIENT, cipher=make_cipher(), idempotency_key="cancel:u"
    )

    cancel_queued_email(database_session, delivery_id=queued.delivery.id, now=NOW)
    database_session.refresh(queued.delivery)

    assert queued.delivery.encrypted_recipient is None
    assert queued.delivery.recipient_key_version is None


def test_sending_or_sent_delivery_cannot_be_canceled(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user
    repository = TransactionalEmailDeliveryRepository(database_session)
    delivery = repository.create_workspace_delivery(
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        idempotency_key="nocancel:1",
    ).delivery

    delivery.status = Status.SENDING
    database_session.flush()
    with pytest.raises(DeliveryStateError):
        repository.cancel_pending(delivery)

    delivery.status = Status.SENT
    delivery.sent_at = NOW
    delivery.retention_expires_at = NOW + SAFE_STATE_RETENTION
    database_session.flush()
    with pytest.raises(DeliveryStateError):
        repository.cancel_pending(delivery)


def test_scrub_recipient_requires_final_state_and_exposes_safe_terminal_state(
    database_session: Session,
) -> None:
    repository = TransactionalEmailDeliveryRepository(database_session)
    delivery = repository.create_unscoped_completion_delivery(
        recipient=RECIPIENT, cipher=make_cipher(), idempotency_key="scrub:1"
    ).delivery

    assert repository.get_terminal_state(delivery.id) is None
    with pytest.raises(DeliveryStateError):
        repository.scrub_recipient(delivery)

    # Finish the delivery and scrub in one statement, as the database requires.
    delivery.status = Status.PERMANENTLY_FAILED
    delivery.failed_at = NOW
    delivery.safe_failure_code = "configuration_invalid"
    delivery.retention_expires_at = NOW + SAFE_STATE_RETENTION
    delivery.encrypted_recipient = None
    delivery.recipient_key_version = None
    database_session.flush()
    repository.scrub_recipient(delivery)

    state = repository.get_terminal_state(delivery.id)
    assert state is not None
    assert state.status is Status.PERMANENTLY_FAILED
    assert state.final_at == NOW
    assert state.safe_failure_code == "configuration_invalid"
    assert not hasattr(state, "encrypted_recipient")


def test_lookup_primitives_and_locking(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user
    repository = TransactionalEmailDeliveryRepository(database_session)
    delivery = repository.create_workspace_delivery(
        workspace_id=workspace.id,
        user_id=user.id,
        event_type=Event.ADMINISTRATOR_PASSWORD_RESET,
        idempotency_key="lookup:1",
    ).delivery

    assert repository.get(delivery.id) is delivery
    assert repository.get(delivery.id, for_update=True) is delivery
    assert repository.get_by_idempotency_key(" lookup:1 ") is delivery
    assert repository.get(delivery.id + 10_000) is None
    assert repository.get_by_idempotency_key("missing") is None


def test_no_recipient_or_secret_in_job_or_delivery_state(database_session: Session) -> None:
    queued = queue_unscoped_completion_email(
        database_session, recipient=RECIPIENT, cipher=make_cipher(), idempotency_key="leak:1"
    )

    assert queued.job is not None
    assert RECIPIENT not in str(queued.job.payload)
    assert RECIPIENT not in (queued.job.idempotency_key or "")
    assert RECIPIENT not in repr(queued.delivery.idempotency_key)
    assert RECIPIENT not in (queued.delivery.encrypted_recipient or "")
