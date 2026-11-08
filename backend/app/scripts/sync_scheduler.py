import argparse
import logging
import signal
from datetime import UTC, datetime
from threading import Event

from app.core.settings import Settings, get_settings
from app.db.session import SessionFactory
from app.services.espn_sync import SyncResult
from app.services.provider_sync_compat import (
    synchronize_active_provider,
)
from app.services.sync_lock import SyncAlreadyRunningError
from app.services.sync_schedule import (
    calculate_next_sync_time,
    seconds_until,
)

LOGGER = logging.getLogger("fantasy_what_if.sync_scheduler")
SHUTDOWN_EVENT = Event()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Run scheduled ESPN fantasy league synchronization.")
    )

    parser.add_argument(
        "--run-now",
        action="store_true",
        help=("Run one incremental synchronization immediately and exit."),
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


def run_incremental_sync(
    settings: Settings,
) -> SyncResult | None:
    LOGGER.info("Starting incremental fantasy provider synchronization.")

    try:
        with SessionFactory() as session:
            result = synchronize_active_provider(
                session=session,
                settings=settings,
                full_sync=False,
            )
    except SyncAlreadyRunningError:
        LOGGER.warning(
            "Synchronization skipped because another process holds the league-season lock."
        )
        return None
    except Exception:
        LOGGER.exception("Scheduled fantasy provider synchronization failed.")
        return None

    LOGGER.info(
        "Synchronization succeeded: current_week=%s, "
        "player_weeks=%s, teams=%s, matchups=%s, "
        "player_week_scores=%s.",
        result.current_week,
        list(result.player_weeks),
        result.teams_upserted,
        result.matchups_upserted,
        result.player_week_scores_upserted,
    )

    return result


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
            "Next synchronization scheduled for %s.",
            target.isoformat(),
        )

        if _wait_for_target(
            delay_seconds=delay,
            poll_seconds=(settings.sync_scheduler_poll_seconds),
        ):
            break

        if SHUTDOWN_EVENT.is_set():
            break

        run_incremental_sync(settings)

    LOGGER.info("Synchronization scheduler stopped.")


def _wait_for_target(
    *,
    delay_seconds: float,
    poll_seconds: int,
) -> bool:
    remaining = delay_seconds

    while remaining > 0:
        if SHUTDOWN_EVENT.wait(
            timeout=min(
                float(poll_seconds),
                remaining,
            )
        ):
            return True

        remaining -= min(
            float(poll_seconds),
            remaining,
        )

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
        result = run_incremental_sync(settings)

        if result is None:
            raise SystemExit(1)

        print("=== IMMEDIATE SYNC COMPLETE ===")
        print(f"League ID: {result.league_id}")
        print(f"Season: {result.season}")
        print(f"Current week: {result.current_week}")
        print(f"Player weeks: {list(result.player_weeks)}")
        return

    run_scheduler(settings)


if __name__ == "__main__":
    main()
