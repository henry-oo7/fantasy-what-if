from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_enums import (
    BackgroundJobType,
)
from app.db.platform_models import BackgroundJob
from app.providers.errors import ProviderError
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from app.repositories.workspace_deletion_repository import (
    WorkspaceDeletionRepository,
)
from app.services.background_job_heartbeat import (
    BackgroundJobHeartbeatError,
    BackgroundJobLeaseHeartbeat,
)
from app.services.onboarding_sync_outcome import apply_initial_sync_outcome

LOGGER = logging.getLogger(__name__)

DEFAULT_RETRY_DELAYS = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
)


class BackgroundJobHandler(Protocol):
    """Execute one detached background-job snapshot."""

    def __call__(
        self,
        job: BackgroundJobSnapshot,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class BackgroundJobSnapshot:
    """Non-secret job data passed outside the claim transaction."""

    id: int
    workspace_id: int | None
    job_type: BackgroundJobType
    payload_version: int
    payload: dict[str, Any]
    attempt_count: int
    maximum_attempts: int


@dataclass(frozen=True, slots=True)
class BackgroundJobExecutionResult:
    """Outcome of one worker polling iteration."""

    job_id: int | None
    claimed: bool
    succeeded: bool
    retry_scheduled: bool
    failed: bool


class BackgroundJobExecutionError(RuntimeError):
    """Safe non-provider failure raised by a job handler."""

    def __init__(
        self,
        *,
        safe_error_code: str,
        safe_error_message: str,
        retryable: bool = False,
    ) -> None:
        normalized_code = safe_error_code.strip()
        normalized_message = " ".join(safe_error_message.strip().split())

        if not normalized_code:
            raise ValueError("safe_error_code cannot be empty.")

        if len(normalized_code) > 100:
            raise ValueError("safe_error_code cannot exceed 100 characters.")

        if not normalized_message:
            raise ValueError("safe_error_message cannot be empty.")

        self.safe_error_code = normalized_code
        self.safe_error_message = normalized_message
        self.retryable = retryable

        super().__init__(normalized_message)


class BackgroundJobDeferredError(RuntimeError):
    """The handler found its job not yet due and did no work.

    The worker returns the job to ``pending`` at ``available_at`` and
    restores the attempt the claim consumed, so early execution never
    spends a terminal attempt.
    """

    def __init__(
        self,
        *,
        available_at: datetime,
        safe_error_code: str,
        safe_error_message: str,
    ) -> None:
        self.available_at = available_at
        self.safe_error_code = safe_error_code
        self.safe_error_message = safe_error_message

        super().__init__(safe_error_message)


class BackgroundJobHandlerNotFoundError(RuntimeError):
    """No executable handler exists for the job type."""


class BackgroundJobOutcomePersistenceError(RuntimeError):
    """The claimed job could not be safely finalized."""


def snapshot_background_job(
    job: BackgroundJob,
) -> BackgroundJobSnapshot:
    if job.id is None:
        raise ValueError("A persisted background job ID is required.")

    return BackgroundJobSnapshot(
        id=job.id,
        workspace_id=job.workspace_id,
        job_type=job.job_type,
        payload_version=job.payload_version,
        payload=dict(job.payload),
        attempt_count=job.attempt_count,
        maximum_attempts=job.maximum_attempts,
    )


def calculate_retry_time(
    *,
    attempt_count: int,
    now: datetime,
    retry_delays: tuple[timedelta, ...] = (DEFAULT_RETRY_DELAYS),
    jitter_ratio: float = 0.2,
    random_source: Random | None = None,
) -> datetime:
    if attempt_count < 1:
        raise ValueError("attempt_count must be positive.")

    if not retry_delays:
        raise ValueError("retry_delays cannot be empty.")

    if not 0 <= jitter_ratio <= 1:
        raise ValueError("jitter_ratio must be between 0 and 1.")

    delay_index = min(
        attempt_count - 1,
        len(retry_delays) - 1,
    )
    base_delay = retry_delays[delay_index]

    if base_delay <= timedelta(0):
        raise ValueError("Retry delays must be positive.")

    source = random_source or Random()
    base_seconds = base_delay.total_seconds()
    jitter_seconds = base_seconds * jitter_ratio * source.uniform(-1.0, 1.0)
    final_seconds = max(
        1.0,
        base_seconds + jitter_seconds,
    )

    return now + timedelta(seconds=final_seconds)


def execute_one_background_job(
    *,
    session_factory: sessionmaker[Session],
    worker_id: str,
    lease_duration: timedelta,
    handlers: Mapping[
        BackgroundJobType,
        BackgroundJobHandler,
    ],
    heartbeat_interval: timedelta = timedelta(seconds=60),
    heartbeat_factory: Callable[
        ...,
        BackgroundJobLeaseHeartbeat,
    ] = BackgroundJobLeaseHeartbeat,
    now: Callable[[], datetime] = (lambda: datetime.now(UTC)),
    retry_delays: tuple[timedelta, ...] = (DEFAULT_RETRY_DELAYS),
    jitter_ratio: float = 0.2,
    random_source: Random | None = None,
) -> BackgroundJobExecutionResult:
    snapshot = _claim_one_job(
        session_factory=session_factory,
        worker_id=worker_id,
        lease_duration=lease_duration,
        now=now(),
    )

    if snapshot is None:
        return BackgroundJobExecutionResult(
            job_id=None,
            claimed=False,
            succeeded=False,
            retry_scheduled=False,
            failed=False,
        )

    handler = handlers.get(snapshot.job_type)

    if handler is None:
        _persist_terminal_failure(
            session_factory=session_factory,
            job_id=snapshot.id,
            worker_id=worker_id,
            safe_error_code="unsupported_job_type",
            safe_error_message=("This background job type is not executable."),
            retryable=False,
            now=now(),
        )

        return BackgroundJobExecutionResult(
            job_id=snapshot.id,
            claimed=True,
            succeeded=False,
            retry_scheduled=False,
            failed=True,
        )

    try:
        return _execute_and_persist_outcome(
            handler=handler,
            snapshot=snapshot,
            session_factory=session_factory,
            worker_id=worker_id,
            lease_duration=lease_duration,
            heartbeat_interval=heartbeat_interval,
            heartbeat_factory=heartbeat_factory,
            now=now,
            retry_delays=retry_delays,
            jitter_ratio=jitter_ratio,
            random_source=random_source,
        )
    except BackgroundJobHeartbeatError, BackgroundJobOutcomePersistenceError:
        # A committed workspace deletion cascades its own job row, so the
        # heartbeat and outcome bookkeeping can no longer find it. The
        # anonymized deletion record is the authoritative completion.
        if not _workspace_deletion_completed(
            session_factory=session_factory,
            snapshot=snapshot,
        ):
            raise

        LOGGER.info(
            "Workspace deletion job completed with its job row removed: "
            "job_id=%s, former_workspace_id=%s, outcome=completed.",
            snapshot.id,
            snapshot.workspace_id,
        )

        return BackgroundJobExecutionResult(
            job_id=snapshot.id,
            claimed=True,
            succeeded=True,
            retry_scheduled=False,
            failed=False,
        )


def _execute_and_persist_outcome(
    *,
    handler: BackgroundJobHandler,
    snapshot: BackgroundJobSnapshot,
    session_factory: sessionmaker[Session],
    worker_id: str,
    lease_duration: timedelta,
    heartbeat_interval: timedelta,
    heartbeat_factory: Callable[
        ...,
        BackgroundJobLeaseHeartbeat,
    ],
    now: Callable[[], datetime],
    retry_delays: tuple[timedelta, ...],
    jitter_ratio: float,
    random_source: Random | None,
) -> BackgroundJobExecutionResult:
    try:
        _execute_handler_with_heartbeat(
            handler=handler,
            snapshot=snapshot,
            session_factory=session_factory,
            worker_id=worker_id,
            lease_duration=lease_duration,
            heartbeat_interval=heartbeat_interval,
            heartbeat_factory=heartbeat_factory,
        )
    except BackgroundJobHeartbeatError:
        raise
    except BackgroundJobDeferredError as exc:
        _persist_deferral(
            session_factory=session_factory,
            job_id=snapshot.id,
            worker_id=worker_id,
            available_at=exc.available_at,
            safe_error_code=exc.safe_error_code,
            safe_error_message=exc.safe_error_message,
        )

        return BackgroundJobExecutionResult(
            job_id=snapshot.id,
            claimed=True,
            succeeded=False,
            retry_scheduled=True,
            failed=False,
        )
    except BackgroundJobExecutionError as exc:
        if exc.retryable and snapshot.attempt_count < snapshot.maximum_attempts:
            retry_at = calculate_retry_time(
                attempt_count=snapshot.attempt_count,
                now=now(),
                retry_delays=retry_delays,
                jitter_ratio=jitter_ratio,
                random_source=random_source,
            )
            _persist_retry(
                session_factory=session_factory,
                job_id=snapshot.id,
                worker_id=worker_id,
                available_at=retry_at,
                safe_error_code=(exc.safe_error_code),
                safe_error_message=(exc.safe_error_message),
            )

            return BackgroundJobExecutionResult(
                job_id=snapshot.id,
                claimed=True,
                succeeded=False,
                retry_scheduled=True,
                failed=False,
            )

        _persist_terminal_failure(
            session_factory=session_factory,
            job_id=snapshot.id,
            worker_id=worker_id,
            safe_error_code=exc.safe_error_code,
            safe_error_message=(exc.safe_error_message),
            retryable=exc.retryable,
            now=now(),
        )

        return BackgroundJobExecutionResult(
            job_id=snapshot.id,
            claimed=True,
            succeeded=False,
            retry_scheduled=False,
            failed=True,
        )
    except ProviderError as exc:
        if exc.retryable and snapshot.attempt_count < snapshot.maximum_attempts:
            retry_at = calculate_retry_time(
                attempt_count=snapshot.attempt_count,
                now=now(),
                retry_delays=retry_delays,
                jitter_ratio=jitter_ratio,
                random_source=random_source,
            )
            _persist_retry(
                session_factory=session_factory,
                job_id=snapshot.id,
                worker_id=worker_id,
                available_at=retry_at,
                safe_error_code=(_provider_error_code(exc)),
                safe_error_message=exc.safe_message,
            )

            return BackgroundJobExecutionResult(
                job_id=snapshot.id,
                claimed=True,
                succeeded=False,
                retry_scheduled=True,
                failed=False,
            )

        _persist_terminal_failure(
            session_factory=session_factory,
            job_id=snapshot.id,
            worker_id=worker_id,
            safe_error_code=_provider_error_code(exc),
            safe_error_message=exc.safe_message,
            retryable=exc.retryable,
            now=now(),
        )

        return BackgroundJobExecutionResult(
            job_id=snapshot.id,
            claimed=True,
            succeeded=False,
            retry_scheduled=False,
            failed=True,
        )
    except Exception:
        _persist_terminal_failure(
            session_factory=session_factory,
            job_id=snapshot.id,
            worker_id=worker_id,
            safe_error_code="unexpected_job_failure",
            safe_error_message=("The background job could not be completed."),
            retryable=False,
            now=now(),
        )

        return BackgroundJobExecutionResult(
            job_id=snapshot.id,
            claimed=True,
            succeeded=False,
            retry_scheduled=False,
            failed=True,
        )

    _persist_success(
        session_factory=session_factory,
        job_id=snapshot.id,
        worker_id=worker_id,
        now=now(),
    )

    return BackgroundJobExecutionResult(
        job_id=snapshot.id,
        claimed=True,
        succeeded=True,
        retry_scheduled=False,
        failed=False,
    )


def _execute_handler_with_heartbeat(
    *,
    handler: BackgroundJobHandler,
    snapshot: BackgroundJobSnapshot,
    session_factory: sessionmaker[Session],
    worker_id: str,
    lease_duration: timedelta,
    heartbeat_interval: timedelta,
    heartbeat_factory: Callable[
        ...,
        BackgroundJobLeaseHeartbeat,
    ],
) -> None:
    heartbeat = heartbeat_factory(
        session_factory=session_factory,
        job_id=snapshot.id,
        worker_id=worker_id,
        lease_duration=lease_duration,
        heartbeat_interval=heartbeat_interval,
    )
    handler_failure: BaseException | None = None

    heartbeat.start()

    try:
        handler(snapshot)
    except BaseException as exc:
        handler_failure = exc
    finally:
        heartbeat.stop()

    heartbeat.raise_if_failed()

    if handler_failure is not None:
        raise handler_failure


def _workspace_deletion_completed(
    *,
    session_factory: sessionmaker[Session],
    snapshot: BackgroundJobSnapshot,
) -> bool:
    """Recognize a committed deletion whose job row cascaded away."""

    if snapshot.job_type is not BackgroundJobType.WORKSPACE_DELETION:
        return False

    with session_factory() as session:
        if session.get(BackgroundJob, snapshot.id) is not None:
            return False

        return WorkspaceDeletionRepository(session).completed_record_exists(snapshot.workspace_id)


def _claim_one_job(
    *,
    session_factory: sessionmaker[Session],
    worker_id: str,
    lease_duration: timedelta,
    now: datetime,
) -> BackgroundJobSnapshot | None:
    with session_factory() as session:
        repository = BackgroundJobRepository(session)
        job = repository.claim_next_available(
            worker_id=worker_id,
            lease_duration=lease_duration,
            now=now,
        )

        if job is None:
            session.rollback()
            return None

        snapshot = snapshot_background_job(job)
        session.commit()

    return snapshot


def _load_claimed_job(
    *,
    session: Session,
    job_id: int,
) -> BackgroundJob:
    job = session.get(
        BackgroundJob,
        job_id,
    )

    if job is None:
        raise BackgroundJobOutcomePersistenceError("The claimed background job no longer exists.")

    return job


def _persist_success(
    *,
    session_factory: sessionmaker[Session],
    job_id: int,
    worker_id: str,
    now: datetime,
) -> None:
    with session_factory() as session:
        repository = BackgroundJobRepository(session)
        job = _load_claimed_job(
            session=session,
            job_id=job_id,
        )

        repository.mark_succeeded(
            job,
            worker_id=worker_id,
            now=now,
        )
        apply_initial_sync_outcome(session, job=job, succeeded=True)
        session.commit()


def _persist_retry(
    *,
    session_factory: sessionmaker[Session],
    job_id: int,
    worker_id: str,
    available_at: datetime,
    safe_error_code: str,
    safe_error_message: str,
) -> None:
    with session_factory() as session:
        repository = BackgroundJobRepository(session)
        job = _load_claimed_job(
            session=session,
            job_id=job_id,
        )

        repository.schedule_retry(
            job,
            worker_id=worker_id,
            available_at=available_at,
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
        )
        session.commit()


def _persist_deferral(
    *,
    session_factory: sessionmaker[Session],
    job_id: int,
    worker_id: str,
    available_at: datetime,
    safe_error_code: str,
    safe_error_message: str,
) -> None:
    with session_factory() as session:
        repository = BackgroundJobRepository(session)
        job = _load_claimed_job(
            session=session,
            job_id=job_id,
        )

        repository.defer_claimed_job(
            job,
            worker_id=worker_id,
            available_at=available_at,
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
        )
        session.commit()


def _persist_terminal_failure(
    *,
    session_factory: sessionmaker[Session],
    job_id: int,
    worker_id: str,
    safe_error_code: str,
    safe_error_message: str,
    retryable: bool,
    now: datetime,
) -> None:
    with session_factory() as session:
        repository = BackgroundJobRepository(session)
        job = _load_claimed_job(
            session=session,
            job_id=job_id,
        )

        repository.mark_failed(
            job,
            worker_id=worker_id,
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
            retryable=retryable,
            now=now,
        )
        apply_initial_sync_outcome(session, job=job, succeeded=False)
        session.commit()


def _provider_error_code(
    error: ProviderError,
) -> str:
    error_name = type(error).__name__

    if error_name.startswith("Provider"):
        error_name = error_name[len("Provider") :]

    if error_name.endswith("Error"):
        error_name = error_name[: -len("Error")]

    characters: list[str] = []

    for index, character in enumerate(error_name):
        if character.isupper() and index > 0 and characters[-1] != "_":
            characters.append("_")

        characters.append(character.casefold())

    normalized = "".join(characters).strip("_")

    return normalized or "provider_error"
