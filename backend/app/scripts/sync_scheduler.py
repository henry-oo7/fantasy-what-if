"""Create durable synchronization jobs on the configured schedule."""

from __future__ import annotations

import argparse
import logging
import signal
from datetime import UTC, datetime
from threading import Event

from app.core.settings import Settings, get_settings
from app.db.session import SessionFactory
from app.services.active_season_context import (
    resolve_active_season_context,
)
from app.services.scheduled_synchronization_jobs import (
    ScheduledSynchronizationBatch,
    ScheduledSynchronizationJob,
    ScheduledSynchronizationTarget,
    enqueue_scheduled_synchronization,
    enqueue_scheduled_synchronization_batch,
)
from app.services.sync_schedule import (
    calculate_next_sync_time,
    seconds_until,
)

LOGGER = logging.getLogger("fantasy_what_if.sync_scheduler")
SHUTDOWN_EVENT = Event()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=("Create durable scheduled synchronization jobs."))
    parser.add_argument(
        "--run-now",
        action="store_true",
        help=("Queue one workspace synchronization immediately and exit."),
    )
    parser.add_argument(
        "--workspace-id",
        type=int,
        help=("Internal workspace ID. Required with --run-now."),
    )
    parser.add_argument(
        "--show-next-run",
        action="store_true",
        help=("Print the next scheduled synchronization time and exit."),
    )

    return parser.parse_args()


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


def install_signal_handlers() -> None:
    signal.signal(
        signal.SIGTERM,
        _handle_shutdown_signal,
    )
    signal.signal(
        signal.SIGINT,
        _handle_shutdown_signal,
    )


def enqueue_scheduled_batch(
    *,
    scheduled_for: datetime,
) -> ScheduledSynchronizationBatch | None:
    """Create one committed job batch for a schedule window."""

    try:
        with SessionFactory() as session:
            batch = enqueue_scheduled_synchronization_batch(
                session,
                scheduled_for=scheduled_for,
            )
            session.commit()
    except Exception:
        LOGGER.exception(
            "Scheduled synchronization job dispatch failed: scheduled_for=%s.",
            scheduled_for.isoformat(),
        )
        return None

    LOGGER.info(
        "Scheduled synchronization jobs committed: "
        "scheduled_for=%s, total=%s, created=%s, "
        "existing=%s.",
        scheduled_for.isoformat(),
        len(batch.jobs),
        batch.created_count,
        batch.existing_count,
    )

    return batch


def enqueue_workspace_now(
    *,
    workspace_id: int,
    scheduled_for: datetime,
) -> ScheduledSynchronizationJob | None:
    """Queue one eligible workspace without provider access."""

    try:
        with SessionFactory() as session:
            context = resolve_active_season_context(
                session=session,
                workspace_id=workspace_id,
            )
            accepted = enqueue_scheduled_synchronization(
                session,
                target=ScheduledSynchronizationTarget(
                    workspace_id=workspace_id,
                    season_id=context.season_id,
                ),
                scheduled_for=scheduled_for,
            )
            session.commit()
    except Exception:
        LOGGER.exception(
            "Immediate scheduled-job creation failed: workspace_id=%s.",
            workspace_id,
        )
        return None

    LOGGER.info(
        "Immediate synchronization job committed: "
        "workspace_id=%s, season_id=%s, job_id=%s, "
        "status=%s, existing=%s.",
        accepted.workspace_id,
        accepted.season_id,
        accepted.job_id,
        accepted.status.value,
        accepted.existing,
    )

    return accepted


def next_scheduled_time(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> datetime:
    current_time = now or datetime.now(UTC)

    return calculate_next_sync_time(
        now=current_time,
        timezone_name=settings.sync_schedule_timezone,
        weekday=settings.sync_schedule_weekday,
        hour=settings.sync_schedule_hour,
        minute=settings.sync_schedule_minute,
    )


def run_scheduler(
    settings: Settings,
) -> None:
    if not settings.sync_scheduler_enabled:
        raise SystemExit(
            "Synchronization scheduler is disabled. "
            "Set SYNC_SCHEDULER_ENABLED=true only in "
            "the dedicated scheduler environment."
        )

    install_signal_handlers()

    LOGGER.info(
        "Synchronization scheduler started: "
        "timezone=%s, weekday=%s, time=%02d:%02d, "
        "poll_seconds=%s.",
        settings.sync_schedule_timezone,
        settings.sync_schedule_weekday,
        settings.sync_schedule_hour,
        settings.sync_schedule_minute,
        settings.sync_scheduler_poll_seconds,
    )

    while not SHUTDOWN_EVENT.is_set():
        now = datetime.now(UTC)
        target = next_scheduled_time(
            settings,
            now=now,
        )
        delay = seconds_until(
            now=now,
            target=target,
        )

        LOGGER.info(
            "Next synchronization job window is %s.",
            target.isoformat(),
        )

        if _wait_for_target(
            delay_seconds=delay,
            poll_seconds=(settings.sync_scheduler_poll_seconds),
        ):
            break

        if SHUTDOWN_EVENT.is_set():
            break

        enqueue_scheduled_batch(
            scheduled_for=target,
        )

    LOGGER.info("Synchronization scheduler stopped.")


def _wait_for_target(
    *,
    delay_seconds: float,
    poll_seconds: int,
) -> bool:
    remaining = delay_seconds

    while remaining > 0:
        wait_seconds = min(
            float(poll_seconds),
            remaining,
        )

        if SHUTDOWN_EVENT.wait(timeout=wait_seconds):
            return True

        remaining -= wait_seconds

    return SHUTDOWN_EVENT.is_set()


def _handle_shutdown_signal(
    signal_number: int,
    _frame: object,
) -> None:
    LOGGER.info(
        "Received shutdown signal %s.",
        signal_number,
    )
    SHUTDOWN_EVENT.set()


def main() -> None:
    arguments = parse_arguments()
    settings = get_settings()

    configure_logging(settings)

    if arguments.show_next_run:
        target = next_scheduled_time(settings)

        print("=== NEXT SCHEDULED SYNC ===")
        print(f"Enabled: {settings.sync_scheduler_enabled}")
        print(f"Time: {target.isoformat()}")
        print(f"Timezone: {settings.sync_schedule_timezone}")
        return

    if arguments.run_now:
        if arguments.workspace_id is None:
            raise SystemExit("--workspace-id is required with --run-now.")

        accepted = enqueue_workspace_now(
            workspace_id=arguments.workspace_id,
            scheduled_for=datetime.now(UTC),
        )

        if accepted is None:
            raise SystemExit(1)

        print("=== IMMEDIATE SYNC JOB ACCEPTED ===")
        print(f"Job ID: {accepted.job_id}")
        print(f"Workspace ID: {accepted.workspace_id}")
        print(f"Season ID: {accepted.season_id}")
        print(f"Status: {accepted.status.value}")
        print(f"Existing: {accepted.existing}")
        return

    run_scheduler(settings)


if __name__ == "__main__":
    main()
