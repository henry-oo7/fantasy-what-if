"""Durable administrator identity-email intents (ADR 0009).

Public identity routes queue delivery; they never create tokens or call a
provider. The transactional-email worker rechecks eligibility, creates the
token hash, and sends.

Delivery idempotency keys contain only internal, non-secret identifiers
(never an email address, token, token hash, slug, password, or display
name):

- Registration verification issuance:
  ``identity-email:administrator_email_verification:user:{user_id}:registration``
- Deliberate verification resend issuance:
  ``identity-email:administrator_email_verification:user:{user_id}:resend-after:{previous_delivery_id}``
- Password-reset issuance:
  ``identity-email:administrator_password_reset:user:{user_id}:after:{previous_delivery_id}``
- Password-changed notice for one successful reset completion:
  ``identity-email:administrator_password_changed:user:{user_id}:reset-token:{consumed_token_id}``

``previous_delivery_id`` is the newest existing delivery of the same event
for the administrator, or ``0`` when none exists. Resend and reset issuance
lock the administrator row, so equivalent concurrent requests serialize.
While the newest delivery is still active (pending, sending, or
retry-scheduled) it and its job are reused; once it is final, the next
deliberate request derives a new key. The unique delivery idempotency key
remains the database backstop.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.auth_models import User
from app.db.platform_enums import TransactionalEmailEventType
from app.db.platform_models import BackgroundJob, TransactionalEmailDelivery
from app.notifications.delivery_job_contract import transactional_email_job_idempotency_key
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
    queue_workspace_email,
)

IDENTITY_EMAIL_KEY_PREFIX = "identity-email"

VERIFICATION = TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION
PASSWORD_RESET = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET
PASSWORD_CHANGED = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_CHANGED


def _require_identifier(name: str, value: int, *, minimum: int = 1) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}.")


def registration_verification_key(user_id: int) -> str:
    _require_identifier("user_id", user_id)
    return f"{IDENTITY_EMAIL_KEY_PREFIX}:{VERIFICATION.value}:user:{user_id}:registration"


def verification_resend_key(user_id: int, previous_delivery_id: int) -> str:
    _require_identifier("user_id", user_id)
    _require_identifier("previous_delivery_id", previous_delivery_id, minimum=0)
    return (
        f"{IDENTITY_EMAIL_KEY_PREFIX}:{VERIFICATION.value}:user:{user_id}"
        f":resend-after:{previous_delivery_id}"
    )


def password_reset_key(user_id: int, previous_delivery_id: int) -> str:
    _require_identifier("user_id", user_id)
    _require_identifier("previous_delivery_id", previous_delivery_id, minimum=0)
    return (
        f"{IDENTITY_EMAIL_KEY_PREFIX}:{PASSWORD_RESET.value}:user:{user_id}"
        f":after:{previous_delivery_id}"
    )


def password_changed_key(user_id: int, reset_token_id: int) -> str:
    _require_identifier("user_id", user_id)
    _require_identifier("reset_token_id", reset_token_id)
    return (
        f"{IDENTITY_EMAIL_KEY_PREFIX}:{PASSWORD_CHANGED.value}:user:{user_id}"
        f":reset-token:{reset_token_id}"
    )


def queue_registration_verification(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
) -> QueuedTransactionalEmail:
    """Queue the first verification email inside the registration transaction."""

    return queue_workspace_email(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=VERIFICATION,
        idempotency_key=registration_verification_key(user_id),
    )


def queue_verification_resend(
    session: Session,
    *,
    administrator: User,
) -> QueuedTransactionalEmail:
    """Create or reuse the active verification delivery for an eligible admin.

    The caller has already checked eligibility. No token is created.
    """

    workspace_id, user_id = _lock_administrator(session, administrator)
    return _issue_or_reuse(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=VERIFICATION,
        key_for=lambda previous: verification_resend_key(user_id, previous),
    )


def queue_password_reset(
    session: Session,
    *,
    administrator: User,
) -> QueuedTransactionalEmail:
    """Create or reuse the active password-reset delivery for an eligible admin.

    The caller has already checked eligibility. No token is created, and
    earlier reset tokens stay valid until a newer delivery is accepted.
    """

    workspace_id, user_id = _lock_administrator(session, administrator)
    return _issue_or_reuse(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=PASSWORD_RESET,
        key_for=lambda previous: password_reset_key(user_id, previous),
    )


def queue_password_changed(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    reset_token_id: int,
) -> QueuedTransactionalEmail:
    """Queue the tokenless password-changed notice for one reset completion."""

    return queue_workspace_email(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=PASSWORD_CHANGED,
        idempotency_key=password_changed_key(user_id, reset_token_id),
    )


def lock_open_password_reset_deliveries(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
) -> list[TransactionalEmailDelivery]:
    """Lock this administrator's nonterminal reset deliveries, oldest first.

    Scoped to one user, one workspace, and the reset event only.
    """

    return list(
        session.scalars(
            select(TransactionalEmailDelivery)
            .where(
                TransactionalEmailDelivery.workspace_id == workspace_id,
                TransactionalEmailDelivery.user_id == user_id,
                TransactionalEmailDelivery.event_type == PASSWORD_RESET,
                TransactionalEmailDelivery.status.in_(NONTERMINAL_STATUSES),
            )
            .order_by(TransactionalEmailDelivery.id)
            .with_for_update()
        )
    )


def cancel_password_reset_deliveries(
    session: Session,
    deliveries: Sequence[TransactionalEmailDelivery],
    *,
    now: datetime,
) -> None:
    """Cancel locked reset deliveries and their not-yet-running jobs.

    A running job is left to the worker: the canceled delivery is
    authoritative, so its outcome is discarded as a non-current attempt.
    """

    repository = TransactionalEmailDeliveryRepository(session)
    jobs = BackgroundJobRepository(session)

    for delivery in deliveries:
        if delivery.event_type is not PASSWORD_RESET:
            raise ValueError("Only password-reset deliveries may be canceled here.")

        repository.cancel_nonterminal(delivery, now=now)
        # Lock the job so a concurrent claim cannot be overwritten; a job a
        # worker already claimed is left running.
        job = session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.workspace_id == delivery.workspace_id,
                BackgroundJob.idempotency_key
                == transactional_email_job_idempotency_key(delivery.id),
                BackgroundJob.status.in_(QUEUED_JOB_STATUSES),
            )
            .with_for_update()
        )

        if job is not None and job.status in QUEUED_JOB_STATUSES:
            jobs.cancel_before_execution(job, now=now)


def _lock_administrator(session: Session, administrator: User) -> tuple[int, int]:
    locked = session.scalar(select(User).where(User.id == administrator.id).with_for_update())

    if locked is None or locked.workspace_id is None:
        raise RuntimeError("The administrator is unavailable for identity email.")

    return locked.workspace_id, locked.id


def _issue_or_reuse(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    event_type: TransactionalEmailEventType,
    key_for: Callable[[int], str],
) -> QueuedTransactionalEmail:
    latest = session.scalar(
        select(TransactionalEmailDelivery)
        .where(
            TransactionalEmailDelivery.user_id == user_id,
            TransactionalEmailDelivery.workspace_id == workspace_id,
            TransactionalEmailDelivery.event_type == event_type,
        )
        .order_by(TransactionalEmailDelivery.id.desc())
        .limit(1)
    )

    if latest is not None and latest.status in NONTERMINAL_STATUSES:
        key = latest.idempotency_key
    else:
        key = key_for(latest.id if latest is not None else 0)

    return queue_workspace_email(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        event_type=event_type,
        idempotency_key=key,
    )
