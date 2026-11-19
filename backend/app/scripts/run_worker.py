"""Run the PostgreSQL-backed background-job worker."""

from __future__ import annotations

import logging
import os
import signal
import socket
from datetime import timedelta
from threading import Event

from app.core.settings import Settings, get_settings
from app.db.session import SessionFactory
from app.services.background_job_handlers import (
    build_background_job_handlers,
)
from app.services.background_job_runner import (
    BackgroundJobRunnerConfiguration,
    run_background_job_worker,
)

LOGGER = logging.getLogger("fantasy_what_if.background_worker")
SHUTDOWN_EVENT = Event()


def configure_logging(
    settings: Settings,
) -> None:
    level = getattr(
        logging,
        settings.app_log_level.upper(),
        logging.INFO,
    )

    logging.basicConfig(
        level=level,
        format=("%(asctime)s %(levelname)s %(name)s %(message)s"),
    )


def build_worker_id(
    configured_worker_id: str | None,
    *,
    hostname: str | None = None,
    process_id: int | None = None,
) -> str:
    if configured_worker_id is not None:
        normalized = configured_worker_id.strip()

        if normalized:
            return normalized

    effective_hostname = hostname if hostname is not None else socket.gethostname()
    effective_process_id = process_id if process_id is not None else os.getpid()

    normalized_hostname = effective_hostname.strip()

    if not normalized_hostname:
        normalized_hostname = "unknown-host"

    generated = f"{normalized_hostname}:{effective_process_id}"

    if len(generated) > 255:
        generated = generated[:255]

    return generated


def install_signal_handlers() -> None:
    signal.signal(
        signal.SIGTERM,
        _handle_shutdown_signal,
    )
    signal.signal(
        signal.SIGINT,
        _handle_shutdown_signal,
    )


def _handle_shutdown_signal(
    signal_number: int,
    _frame: object,
) -> None:
    LOGGER.info(
        "Received worker shutdown signal %s.",
        signal_number,
    )
    SHUTDOWN_EVENT.set()


def run_worker(
    settings: Settings,
) -> None:
    if not settings.background_worker_enabled:
        raise SystemExit(
            "Background worker is disabled. "
            "Set BACKGROUND_WORKER_ENABLED=true only "
            "for the dedicated worker process."
        )

    worker_id = build_worker_id(settings.background_worker_id)
    configuration = BackgroundJobRunnerConfiguration(
        worker_id=worker_id,
        lease_duration=timedelta(seconds=(settings.background_worker_lease_seconds)),
        heartbeat_interval=timedelta(seconds=(settings.background_worker_heartbeat_seconds)),
        recovery_interval=timedelta(seconds=(settings.background_worker_recovery_interval_seconds)),
        recovery_retry_delay=timedelta(
            seconds=(settings.background_worker_recovery_retry_delay_seconds)
        ),
        recovery_batch_size=(settings.background_worker_recovery_batch_size),
        poll_seconds=(settings.background_worker_poll_seconds),
        database_backoff_seconds=(settings.background_worker_database_backoff_seconds),
    )
    handlers = build_background_job_handlers(
        session_factory=SessionFactory,
        settings=settings,
    )

    install_signal_handlers()

    run_background_job_worker(
        session_factory=SessionFactory,
        configuration=configuration,
        handlers=handlers,
        shutdown_event=SHUTDOWN_EVENT,
    )


def main() -> None:
    settings = get_settings()

    configure_logging(settings)
    run_worker(settings)


if __name__ == "__main__":
    main()
