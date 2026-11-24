"""Finalize a delivery whose transactional-email job ended terminally.

Called only from the worker's terminal-failure and expired-lease
transactions, so the job state and the delivery state commit together and
no delivery is left ``pending``, ``retry_scheduled``, or ``sending`` after
its only job is gone. It imports nothing from the worker (no import cycle).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.platform_enums import (
    BackgroundJobType,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailScope,
)
from app.db.platform_models import BackgroundJob
from app.notifications.delivery_job_contract import validate_transactional_email_job_payload
from app.repositories.transactional_email_delivery_repository import (
    NONTERMINAL_STATUSES,
    TransactionalEmailDeliveryRepository,
)

JOB_FAILED_CODE = "delivery_job_failed"
UNKNOWN_OUTCOME_CODE = "provider_timeout_unknown"


def apply_transactional_email_job_failure(session: Session, *, job: BackgroundJob) -> None:
    if job.job_type is not BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY:
        return

    try:
        delivery_id = validate_transactional_email_job_payload(job.payload)
    except ValueError:
        return

    deliveries = TransactionalEmailDeliveryRepository(session)
    delivery = deliveries.get(delivery_id, for_update=True)

    if delivery is None or delivery.status not in NONTERMINAL_STATUSES:
        return

    # A job that does not own the delivery must never change it.
    if job.workspace_id is None:
        owned = delivery.workspace_id is None and delivery.scope is TransactionalEmailScope.UNSCOPED
    else:
        owned = (
            delivery.workspace_id == job.workspace_id
            and delivery.scope is TransactionalEmailScope.WORKSPACE
        )

    if not owned:
        return

    deliveries.mark_permanently_failed(
        delivery,
        safe_failure_code=(
            UNKNOWN_OUTCOME_CODE
            if delivery.status is TransactionalEmailDeliveryStatus.SENDING
            else JOB_FAILED_CODE
        ),
    )
