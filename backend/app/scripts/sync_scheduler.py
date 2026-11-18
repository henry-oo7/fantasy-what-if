import argparse
import logging
import signal
from datetime import UTC, datetime
from threading import Event

from sqlalchemy import select

from app.core.settings import Settings, get_settings
from app.db.platform_enums import InstallationStatus
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.services.active_provider_sync import (
    synchronize_active_season,
)
from app.services.provider_sync import ProviderSyncResult
from app.services.sync_lock import SyncAlreadyRunningError
from app.services.sync_schedule import (
    calculate_next_sync_time,
    seconds_until,
)

LOGGER = logging.getLogger("fantasy_what_if.sync_scheduler")
SHUTDOWN_EVENT = Event()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=("Run scheduled active-provider synchronization."))

    parser.add_argument(
        "--run-now",
        action="store_true",
        help=("Run one workspace synchronization immediately and exit."),
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


def list_active_workspace_ids() -> tuple[int, ...]:
    with SessionFactory() as session:
        workspace_ids = session.scalars(
            select(Installation.id)
            .where(
                Installation.status == InstallationStatus.ACTIVE,
            )
            .order_by(Installation.id)
        ).all()

    return tuple(workspace_ids)


def run_incremental_sync(
    settings: Settings,
    *,
    workspace_id: int,
) -> ProviderSyncResult | None:
    LOGGER.info(
        "Starting incremental synchronization: workspace_id=%s.",
        workspace_id,
    )

    try:
        with SessionFactory() as session:
            result = synchronize_active_season(
                session=session,
                workspace_id=workspace_id,
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
        "Synchronization succeeded: workspace_id=%s, "
        "current_week=%s, player_weeks=%s, teams=%s, "
        "matchups=%s, player_week_scores=%s.",
        workspace_id,
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

        workspace_ids = list_active_workspace_ids()

        LOGGER.info(
            "Dispatching sequential synchronization for %s active workspaces.",
            len(workspace_ids),
        )

        for workspace_id in workspace_ids:
            if SHUTDOWN_EVENT.is_set():
                break

            run_incremental_sync(
                settings,
                workspace_id=workspace_id,
            )

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
        if arguments.workspace_id is None:
            raise SystemExit("--workspace-id is required with --run-now.")

        result = run_incremental_sync(
            settings,
            workspace_id=arguments.workspace_id,
        )

        if result is None:
            raise SystemExit(1)

        print("=== IMMEDIATE SYNC COMPLETE ===")
        print(f"Provider: {result.provider.value}")
        print(f"Provider league ID: {result.provider_league_id}")
        print(f"Provider season ID: {result.provider_season_id}")
        print(f"Internal season ID: {result.season_id}")
        print(f"Current week: {result.current_week}")
        print(f"Player weeks: {list(result.player_weeks)}")
        return

    run_scheduler(settings)


if __name__ == "__main__":
    main()
