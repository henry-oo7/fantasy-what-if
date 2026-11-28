"""Atomic transactional-email delivery intent and job creation.

Callers own the surrounding transaction. These functions flush but never
commit unless ``commit=True``, so a delivery and its job become visible
together with the caller's other writes (for example the FR-20 final
deletion transaction) or not at all. No provider is called and no token is
generated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobType,
    TransactionalEmailEventType,
)
from app.db.platform_models import BackgroundJob, TransactionalEmailDelivery
from app.notifications.delivery_job_contract import (
    build_transactional_email_job_payload,
    transactional_email_job_idempotency_key,
)
from app.notifications.recipient_encryption import TransactionalEmailRecipientCipher
from app.repositories.background_job_repository import (
    QUEUED_JOB_STATUSES,
    BackgroundJobRepository,
)
from app.repositories.transactional_email_delivery_repository import (
    NONTERMINAL_STATUSES,
    CreateDeliveryResult,
    TransactionalEmailDeliveryRepository,
)


@dataclass(frozen=True, slots=True)
class QueuedTransactionalEmail:
    """A delivery and its active job (None once the delivery is final)."""

    delivery: TransactionalEmailDelivery
    job: BackgroundJob | None
    existing: bool


def queue_workspace_email(
    session: Session,
    *,
    workspace_id: int,
    user_id: int | None,
    event_type: TransactionalEmailEventType,
    idempotency_key: str,
    available_at: datetime | None = None,
    commit: bool = False,
) -> QueuedTransactionalEmail:
    result = TransactionalEmailDeliveryRepository(session).create_workspace_delivery(
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        available_at=available_at,
    )

    return _finish(session, result, commit=commit)


def queue_user_email(
    session: Session,
    *,
    user_id: int,
    event_type: TransactionalEmailEventType,
    idempotency_key: str,
    available_at: datetime | None = None,
    commit: bool = False,
) -> QueuedTransactionalEmail:
    """Queue a workspace-free personal identity email for one user."""

    result = TransactionalEmailDeliveryRepository(session).create_user_delivery(
        user_id=user_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        available_at=available_at,
    )

    return _finish(session, result, commit=commit)


def queue_unscoped_completion_email(
    session: Session,
    *,
    recipient: str,
    cipher: TransactionalEmailRecipientCipher,
    idempotency_key: str,
    available_at: datetime | None = None,
    commit: bool = False,
) -> QueuedTransactionalEmail:
    result = TransactionalEmailDeliveryRepository(session).create_unscoped_completion_delivery(
        recipient=recipient,
        cipher=cipher,
        idempotency_key=idempotency_key,
        available_at=available_at,
    )

    return _finish(session, result, commit=commit)


def cancel_queued_email(
    session: Session,
    *,
    delivery_id: int,
    now: datetime | None = None,
    commit: bool = False,
) -> bool:
    """Cancel a pending delivery together with its queued job."""

    timestamp = now or datetime.now(UTC)
    deliveries = TransactionalEmailDeliveryRepository(session)
    delivery = deliveries.get(delivery_id, for_update=True)

    if delivery is None:
        return False

    canceled = deliveries.cancel_pending(delivery, now=timestamp)

    jobs = BackgroundJobRepository(session)
    job = jobs.get_active_by_idempotency_key(transactional_email_job_idempotency_key(delivery.id))

    if job is not None and job.status in QUEUED_JOB_STATUSES:
        jobs.cancel_before_execution(job, now=timestamp)

    if commit:
        session.commit()

    return canceled


def _finish(
    session: Session,
    result: CreateDeliveryResult,
    *,
    commit: bool,
) -> QueuedTransactionalEmail:
    delivery = result.delivery
    job: BackgroundJob | None = None

    if delivery.status in NONTERMINAL_STATUSES:
        job = (
            BackgroundJobRepository(session)
            .enqueue(
                workspace_id=delivery.workspace_id,
                job_type=BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY,
                payload=build_transactional_email_job_payload(delivery.id),
                idempotency_key=transactional_email_job_idempotency_key(delivery.id),
                available_at=delivery.available_at,
            )
            .job
        )

    if commit:
        session.commit()

    return QueuedTransactionalEmail(delivery=delivery, job=job, existing=result.existing)
