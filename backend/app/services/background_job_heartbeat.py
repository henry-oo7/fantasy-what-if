from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread

from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_models import BackgroundJob
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)

HEARTBEAT_THREAD_NAME_PREFIX = "background-job-heartbeat"


class BackgroundJobHeartbeatError(RuntimeError):
    """A running job's lease could not be renewed safely."""


class BackgroundJobLeaseHeartbeat:
    """Renew one running job lease in short transactions."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        job_id: int,
        worker_id: str,
        lease_duration: timedelta,
        heartbeat_interval: timedelta,
        now: Callable[[], datetime] = (lambda: datetime.now(UTC)),
    ) -> None:
        if job_id < 1:
            raise ValueError("job_id must be positive.")

        normalized_worker_id = worker_id.strip()

        if not normalized_worker_id:
            raise ValueError("worker_id cannot be empty.")

        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive.")

        if heartbeat_interval <= timedelta(0):
            raise ValueError("heartbeat_interval must be positive.")

        if heartbeat_interval >= lease_duration:
            raise ValueError("heartbeat_interval must be shorter than lease_duration.")

        self._session_factory = session_factory
        self._job_id = job_id
        self._worker_id = normalized_worker_id
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._now = now

        self._stop_event = Event()
        self._failure_lock = Lock()
        self._failure: BaseException | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("The background-job heartbeat has already started.")

        self._thread = Thread(
            target=self._run,
            name=(f"{HEARTBEAT_THREAD_NAME_PREFIX}-{self._job_id}"),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join()

    def raise_if_failed(self) -> None:
        with self._failure_lock:
            failure = self._failure

        if failure is not None:
            raise BackgroundJobHeartbeatError(
                "The background job lease could not be renewed safely."
            ) from failure

    def renew_once(self) -> None:
        with self._session_factory() as session:
            job = session.get(
                BackgroundJob,
                self._job_id,
            )

            if job is None:
                raise BackgroundJobHeartbeatError("The running background job no longer exists.")

            BackgroundJobRepository(session).renew_lease(
                job,
                worker_id=self._worker_id,
                lease_duration=self._lease_duration,
                now=self._now(),
            )
            session.commit()

    def _run(self) -> None:
        interval_seconds = self._heartbeat_interval.total_seconds()

        while not self._stop_event.wait(timeout=interval_seconds):
            try:
                self.renew_once()
            except BaseException as exc:
                with self._failure_lock:
                    self._failure = exc

                self._stop_event.set()
                return
