"""FR-20 final workspace deletion after the grace period.

The ``workspace_deletion`` job handler derives every decision from
persisted rows: the job payload carries nothing but its version. Final
deletion runs in one explicit transaction that locks the workspace row
first, the same lock cancellation takes, so the database cutoff is
authoritative: cancellation wins only while ``now < scheduled_for`` and
deletion only when ``now >= scheduled_for``.

A committed deletion cascades its own job row. The worker then
recognizes completion through the anonymized deletion record rather than
the job row, and never repeats tenant deletion.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_enums import BackgroundJobStatus, BackgroundJobType
from app.notifications.recipient_encryption import (
    TransactionalEmailKeyringConfigurationError,
    TransactionalEmailRecipientCipher,
    TransactionalEmailRecipientError,
)
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.workspace_deletion_repository import WorkspaceDeletionRepository
from app.services.background_job_worker import (
    BackgroundJobDeferredError,
    BackgroundJobExecutionError,
    BackgroundJobSnapshot,
)
from app.services.workspace_deletion import workspace_deletion_idempotency_key
from app.services.workspace_deletion_email import (
    DeletionEmailIntentError,
    queue_deletion_completion_email,
)
from app.services.workspace_lifecycle import is_deletion_pending

LOGGER = logging.getLogger(__name__)

RecipientCipherFactory = Callable[[], TransactionalEmailRecipientCipher]

WORKSPACE_DELETION_PAYLOAD_VERSION = 1
WORKSPACE_DELETION_RECORD_RETENTION = timedelta(days=365)


class WorkspaceDeletionOutcomeKind(StrEnum):
    DELETED = "deleted"
    ALREADY_COMPLETED = "already_completed"
    NOT_PENDING = "not_pending"


def _terminal(code: str, message: str) -> BackgroundJobExecutionError:
    return BackgroundJobExecutionError(
        safe_error_code=code,
        safe_error_message=message,
        retryable=False,
    )


def validate_workspace_deletion_payload(job: BackgroundJobSnapshot) -> None:
    """Accept only the empty versioned payload the request slice enqueues.

    The workspace is the job's own ``workspace_id`` column. Any payload
    key (email, slug, credentials, IDs, instructions) is rejected.
    """

    if job.payload_version != WORKSPACE_DELETION_PAYLOAD_VERSION:
        raise _terminal(
            "unsupported_payload_version",
            "The background job payload version is not supported.",
        )

    if not isinstance(job.payload, dict) or job.payload:
        raise _terminal("invalid_job_payload", "The background job payload is invalid.")


def execute_workspace_deletion(
    session: Session,
    *,
    workspace_id: int,
    job_id: int,
    attempt_count: int,
    now: datetime,
    recipient_cipher_factory: RecipientCipherFactory,
) -> WorkspaceDeletionOutcomeKind:
    """Run the ordered final deletion inside the caller's transaction.

    The caller commits on return and rolls back on any exception, so no
    destructive statement survives a failure.
    """

    repository = WorkspaceDeletionRepository(session)

    # 1. Lock and recheck the workspace.
    workspace = repository.lock_workspace(workspace_id)

    if workspace is None:
        if repository.completed_record_exists(workspace_id):
            return WorkspaceDeletionOutcomeKind.ALREADY_COMPLETED

        raise _terminal(
            "workspace_deletion_workspace_missing",
            "The workspace for this deletion job is unavailable.",
        )

    if not is_deletion_pending(workspace):
        # Cancellation won before the cutoff.
        return WorkspaceDeletionOutcomeKind.NOT_PENDING

    requested_at = workspace.deletion_requested_at
    scheduled_for = workspace.deletion_scheduled_for

    if requested_at is None or scheduled_for is None:
        raise _terminal(
            "workspace_deletion_state_invalid",
            "The workspace deletion schedule is incomplete.",
        )

    if now < scheduled_for:
        raise BackgroundJobDeferredError(
            available_at=scheduled_for,
            safe_error_code="workspace_deletion_not_due",
            safe_error_message="The workspace deletion is not due yet.",
        )

    job = repository.lock_job(job_id)

    if (
        job is None
        or job.workspace_id != workspace_id
        or job.job_type is not BackgroundJobType.WORKSPACE_DELETION
        or job.status is not BackgroundJobStatus.RUNNING
        or job.attempt_count != attempt_count
    ):
        raise _terminal(
            "workspace_deletion_claim_lost",
            "The workspace deletion job is no longer claimed by this attempt.",
        )

    # 2. Capture only the anonymized record fields before rows disappear.
    claim = repository.get_claim(workspace_id)

    if claim is None:
        raise _terminal(
            "workspace_deletion_claim_missing",
            "The workspace has no provider league claim to release.",
        )

    provider = claim.provider
    normalized_provider_league_id = claim.normalized_provider_league_id
    completed_at = now
    retention_expires_at = completed_at + WORKSPACE_DELETION_RECORD_RETENTION

    # 2a. The durable completion notice is created before any tenant row is
    # deleted, in this same transaction: the commit that removes the tenant
    # is the commit that creates the intent, and any failure rolls back both.
    _queue_completion_notice(
        session,
        repository,
        workspace_id=workspace_id,
        scheduled_for=scheduled_for,
        recipient_cipher_factory=recipient_cipher_factory,
        now=now,
    )

    # 3. Connection-linked sync runs, including nullable-season runs.
    repository.delete_connection_sync_runs(workspace_id)

    # 4. Leagues; seasons and imported domain data cascade.
    repository.delete_leagues(workspace_id)

    # 5. The RESTRICT reference must be gone before connections cascade.
    if repository.count_seasons_referencing_connections(workspace_id):
        raise _terminal(
            "workspace_deletion_season_reference_remaining",
            "A season still references a workspace provider connection.",
        )

    # 6. One anonymized completion record.
    repository.add_completed_record(
        former_workspace_id=workspace_id,
        provider=provider,
        normalized_provider_league_id=normalized_provider_league_id,
        requested_at=requested_at,
        scheduled_for=scheduled_for,
        completed_at=completed_at,
        retention_expires_at=retention_expires_at,
    )

    # 7. The workspace row; connections, users, sessions, tokens, claim,
    # scoped email deliveries, and background jobs (including this one)
    # cascade. Only the unscoped completion delivery and its job remain.
    if repository.delete_workspace(workspace_id) != 1:
        raise _terminal(
            "workspace_deletion_workspace_missing",
            "The workspace for this deletion job is unavailable.",
        )

    return WorkspaceDeletionOutcomeKind.DELETED


def _queue_completion_notice(
    session: Session,
    repository: WorkspaceDeletionRepository,
    *,
    workspace_id: int,
    scheduled_for: datetime,
    recipient_cipher_factory: RecipientCipherFactory,
    now: datetime,
) -> None:
    """Encrypt the administrator address and queue the unscoped notice.

    The plaintext address exists only in memory for the encryption call.
    The delivery ID is allocated before insert and bound into the
    ciphertext; the delivery and its NULL-workspace job are flushed, not
    committed. Every failure is terminal and rolls back the deletion.
    """

    administrators = repository.lock_administrators(workspace_id)

    if len(administrators) != 1 or not administrators[0].normalized_email:
        raise _terminal(
            "workspace_deletion_recipient_unavailable",
            "The workspace deletion notice recipient is unavailable.",
        )

    try:
        cipher = recipient_cipher_factory()
        queue_deletion_completion_email(
            session,
            former_workspace_id=workspace_id,
            recipient=administrators[0].normalized_email,
            cipher=cipher,
            scheduled_for=scheduled_for,
            now=now,
        )
    except (
        TransactionalEmailKeyringConfigurationError,
        TransactionalEmailRecipientError,
        DeletionEmailIntentError,
    ):
        raise _terminal(
            "workspace_deletion_notice_unavailable",
            "The workspace deletion notice could not be queued.",
        ) from None


class WorkspaceDeletionJobHandler:
    """Execute one ``workspace_deletion`` job in a single transaction."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        recipient_cipher_factory: RecipientCipherFactory,
        now: Callable[[], datetime] = (lambda: datetime.now(UTC)),
    ) -> None:
        self.session_factory = session_factory
        self.recipient_cipher_factory = recipient_cipher_factory
        self.now = now

    def __call__(self, job: BackgroundJobSnapshot) -> None:
        validate_workspace_deletion_payload(job)

        with self.session_factory() as session:
            try:
                outcome = execute_workspace_deletion(
                    session,
                    workspace_id=job.workspace_id,
                    job_id=job.id,
                    attempt_count=job.attempt_count,
                    now=self.now(),
                    recipient_cipher_factory=self.recipient_cipher_factory,
                )
                # 8. Commit.
                session.commit()
            except OperationalError:
                session.rollback()
                # Deadlocks, lock or serialization failures, and lost
                # connections are retried; raw database text is dropped.
                raise BackgroundJobExecutionError(
                    safe_error_code="workspace_deletion_database_retryable",
                    safe_error_message="The workspace deletion could not complete. It will retry.",
                    retryable=True,
                ) from None
            except IntegrityError:
                session.rollback()
                raise _terminal(
                    "workspace_deletion_constraint_failed",
                    "The workspace deletion violated a database constraint.",
                ) from None

        LOGGER.info(
            "Workspace deletion job finished: job_id=%s, workspace_id=%s, outcome=%s.",
            job.id,
            job.workspace_id,
            outcome.value,
        )


class WorkspaceDeletionReenqueueRefusal(StrEnum):
    WORKSPACE_NOT_FOUND = "workspace_not_found"
    DELETION_ALREADY_COMPLETED = "deletion_already_completed"
    NOT_DELETION_PENDING = "workspace_not_deletion_pending"
    NOT_DUE = "deletion_not_due"


class WorkspaceDeletionReenqueueRefusedError(RuntimeError):
    def __init__(self, refusal: WorkspaceDeletionReenqueueRefusal) -> None:
        self.refusal = refusal

        super().__init__(refusal.value)


@dataclass(frozen=True, slots=True)
class WorkspaceDeletionReenqueueResult:
    job_id: int
    status: BackgroundJobStatus
    existing: bool


def reenqueue_workspace_deletion(
    session: Session,
    *,
    workspace_id: int,
    now: datetime | None = None,
) -> WorkspaceDeletionReenqueueResult:
    """Operator recovery: create or return the one active deletion job.

    Never deletes data and never changes workspace status. The caller
    commits. The workspace lock serializes with cancellation and with a
    running final deletion.
    """

    current_time = now or datetime.now(UTC)
    deletion = WorkspaceDeletionRepository(session)
    workspace = deletion.lock_workspace(workspace_id)

    if deletion.completed_record_exists(workspace_id):
        raise WorkspaceDeletionReenqueueRefusedError(
            WorkspaceDeletionReenqueueRefusal.DELETION_ALREADY_COMPLETED
        )

    if workspace is None:
        raise WorkspaceDeletionReenqueueRefusedError(
            WorkspaceDeletionReenqueueRefusal.WORKSPACE_NOT_FOUND
        )

    if (
        not is_deletion_pending(workspace)
        or workspace.deletion_requested_at is None
        or workspace.deletion_scheduled_for is None
    ):
        raise WorkspaceDeletionReenqueueRefusedError(
            WorkspaceDeletionReenqueueRefusal.NOT_DELETION_PENDING
        )

    if current_time < workspace.deletion_scheduled_for:
        raise WorkspaceDeletionReenqueueRefusedError(WorkspaceDeletionReenqueueRefusal.NOT_DUE)

    result = BackgroundJobRepository(session).enqueue(
        workspace_id=workspace.id,
        job_type=BackgroundJobType.WORKSPACE_DELETION,
        payload={},
        payload_version=WORKSPACE_DELETION_PAYLOAD_VERSION,
        idempotency_key=workspace_deletion_idempotency_key(workspace.id),
        available_at=current_time,
    )

    return WorkspaceDeletionReenqueueResult(
        job_id=result.job.id,
        status=result.job.status,
        existing=result.existing,
    )
