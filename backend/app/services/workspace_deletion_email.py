"""FR-20 deletion email intents (ADR 0009).

Every function runs inside the caller's deletion transaction: the
request, cancellation, or final-deletion transaction. Nothing commits
here and no provider is called; the transactional-email worker sends
later and rechecks eligibility at send time.

Generation boundary
-------------------

One deletion request is identified by the persisted, normalized
``deletion_requested_at`` (and its derived ``deletion_scheduled_for``).
Delivery idempotency keys embed that boundary, so the worker recognizes a
stale intent by recomputing the key from the workspace's current schedule.
Keys contain only internal, non-secret identifiers (never an email,
slug, password, provider League ID, token, or ciphertext):

- ``identity-email:workspace_deletion_requested:workspace:{id}:request:{requested_at}``
- ``identity-email:workspace_deletion_reminder:workspace:{id}:scheduled:{scheduled_for}``
- ``identity-email:workspace_deletion_canceled:workspace:{id}:request:{requested_at}``
- ``identity-email:workspace_deletion_completed:former-workspace:{id}:scheduled:{scheduled_for}``

Timestamps are normalized to UTC with microsecond precision, for example
``20261124T120000.000000Z``.

Reminder timing
---------------

The reminder becomes available at ``scheduled_for - 7 days``. If that time
is not later than the request itself (only possible with a grace period of
seven days or less, for example operator repair or a future configuration),
no reminder is created: the confirmation already states the deletion date,
and a reminder is never scheduled in the past or sent twice.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.platform_enums import TransactionalEmailEventType
from app.db.platform_models import BackgroundJob, TransactionalEmailDelivery
from app.notifications.delivery_job_contract import transactional_email_job_idempotency_key
from app.notifications.recipient_encryption import TransactionalEmailRecipientCipher
from app.repositories.background_job_repository import (
    QUEUED_JOB_STATUSES,
    BackgroundJobRepository,
)
from app.repositories.transactional_email_delivery_repository import (
    NONTERMINAL_STATUSES,
    TransactionalEmailDeliveryRepository,
)
from app.services.transactional_email_intents import (
    QueuedTransactionalEmail,
    queue_unscoped_completion_email,
    queue_workspace_email,
)

KEY_PREFIX = "identity-email"
REMINDER_LEAD_TIME = timedelta(days=7)

REQUESTED = TransactionalEmailEventType.WORKSPACE_DELETION_REQUESTED
REMINDER = TransactionalEmailEventType.WORKSPACE_DELETION_REMINDER
CANCELED = TransactionalEmailEventType.WORKSPACE_DELETION_CANCELED
COMPLETED = TransactionalEmailEventType.WORKSPACE_DELETION_COMPLETED

SCOPED_DELETION_EVENTS = frozenset({REQUESTED, REMINDER, CANCELED})

# Workspace intents that are stale once a new deletion request starts.
# Verification can never be eligible for an active or pending workspace;
# earlier deletion-request, reminder, and cancellation notices describe a
# superseded lifecycle. Password reset and password-changed notices stay:
# recovery remains available during the grace period (FR-20) and the
# worker rechecks their eligibility at send time.
STALE_ON_DELETION_REQUEST = frozenset(
    {
        TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION,
        REQUESTED,
        REMINDER,
        CANCELED,
    }
)

# Intents suppressed by cancellation: every confirmation and reminder of
# the canceled deletion (and of any older generation still open).
STALE_ON_DELETION_CANCEL = frozenset({REQUESTED, REMINDER})


class DeletionEmailIntentError(RuntimeError):
    """A required deletion email intent could not be created as new and active."""


def generation_marker(timestamp: datetime) -> str:
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError("The deletion generation timestamp must be timezone-aware.")

    return timestamp.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _require_workspace_id(workspace_id: int) -> None:
    if not isinstance(workspace_id, int) or isinstance(workspace_id, bool) or workspace_id < 1:
        raise ValueError("workspace_id must be a positive integer.")


def deletion_requested_key(workspace_id: int, requested_at: datetime) -> str:
    _require_workspace_id(workspace_id)
    return (
        f"{KEY_PREFIX}:{REQUESTED.value}:workspace:{workspace_id}"
        f":request:{generation_marker(requested_at)}"
    )


def deletion_reminder_key(workspace_id: int, scheduled_for: datetime) -> str:
    _require_workspace_id(workspace_id)
    return (
        f"{KEY_PREFIX}:{REMINDER.value}:workspace:{workspace_id}"
        f":scheduled:{generation_marker(scheduled_for)}"
    )


def deletion_canceled_key(workspace_id: int, requested_at: datetime) -> str:
    _require_workspace_id(workspace_id)
    return (
        f"{KEY_PREFIX}:{CANCELED.value}:workspace:{workspace_id}"
        f":request:{generation_marker(requested_at)}"
    )


def deletion_completed_key(workspace_id: int, scheduled_for: datetime) -> str:
    _require_workspace_id(workspace_id)
    return (
        f"{KEY_PREFIX}:{COMPLETED.value}:former-workspace:{workspace_id}"
        f":scheduled:{generation_marker(scheduled_for)}"
    )


def reminder_time(*, requested_at: datetime, scheduled_for: datetime) -> datetime | None:
    """Reminder availability, or None when no meaningful reminder fits."""

    reminder_at = scheduled_for - REMINDER_LEAD_TIME

    if reminder_at <= requested_at:
        return None

    return reminder_at


def queue_deletion_request_emails(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    requested_at: datetime,
    scheduled_for: datetime,
) -> tuple[QueuedTransactionalEmail, QueuedTransactionalEmail | None]:
    """Create the confirmation and reminder intents for one new request.

    Raises ``DeletionEmailIntentError`` (so the caller rolls the request
    back) unless each intent is a new, active delivery with a queued job.
    """

    confirmation = _require_new(
        queue_workspace_email(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type=REQUESTED,
            idempotency_key=deletion_requested_key(workspace_id, requested_at),
            available_at=requested_at,
        )
    )

    reminder_at = reminder_time(requested_at=requested_at, scheduled_for=scheduled_for)

    if reminder_at is None:
        return confirmation, None

    reminder = _require_new(
        queue_workspace_email(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type=REMINDER,
            idempotency_key=deletion_reminder_key(workspace_id, scheduled_for),
            available_at=reminder_at,
        )
    )

    return confirmation, reminder


def queue_deletion_canceled_email(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    requested_at: datetime,
    now: datetime,
) -> QueuedTransactionalEmail:
    return _require_new(
        queue_workspace_email(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            event_type=CANCELED,
            idempotency_key=deletion_canceled_key(workspace_id, requested_at),
            available_at=now,
        )
    )


def queue_deletion_completion_email(
    session: Session,
    *,
    former_workspace_id: int,
    recipient: str,
    cipher: TransactionalEmailRecipientCipher,
    scheduled_for: datetime,
    now: datetime,
) -> QueuedTransactionalEmail:
    """Create the unscoped, encrypted completion intent and its NULL-workspace job."""

    return _require_new(
        queue_unscoped_completion_email(
            session,
            recipient=recipient,
            cipher=cipher,
            idempotency_key=deletion_completed_key(former_workspace_id, scheduled_for),
            available_at=now,
        )
    )


def suppress_workspace_emails(
    session: Session,
    *,
    workspace_id: int,
    event_types: Iterable[TransactionalEmailEventType],
    now: datetime,
) -> int:
    """Cancel one workspace's nonterminal deliveries of the given events.

    Deliveries are locked in ID order. A delivery whose job a worker already
    claimed is canceled too; the running job is not rewritten, and the
    worker discards its outcome as a non-current attempt. Queued jobs are
    canceled with their delivery. Other workspaces are never touched.
    """

    _require_workspace_id(workspace_id)
    events = tuple(event_types)

    if not events:
        return 0

    deliveries = list(
        session.scalars(
            select(TransactionalEmailDelivery)
            .where(
                TransactionalEmailDelivery.workspace_id == workspace_id,
                TransactionalEmailDelivery.event_type.in_(events),
                TransactionalEmailDelivery.status.in_(NONTERMINAL_STATUSES),
            )
            .order_by(TransactionalEmailDelivery.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )

    repository = TransactionalEmailDeliveryRepository(session)
    jobs = BackgroundJobRepository(session)

    for delivery in deliveries:
        repository.cancel_nonterminal(delivery, now=now)
        job = session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.workspace_id == workspace_id,
                BackgroundJob.idempotency_key
                == transactional_email_job_idempotency_key(delivery.id),
                BackgroundJob.status.in_(QUEUED_JOB_STATUSES),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        if job is not None and job.status in QUEUED_JOB_STATUSES:
            jobs.cancel_before_execution(job, now=now)

    return len(deliveries)


def _require_new(queued: QueuedTransactionalEmail) -> QueuedTransactionalEmail:
    if queued.existing or queued.job is None:
        raise DeletionEmailIntentError("A required deletion email intent was not created.")

    return queued
