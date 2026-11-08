import argparse

from app.core.settings import get_settings
from app.db.session import SessionFactory
from app.services.provider_sync_compat import (
    synchronize_active_provider,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Synchronize ESPN fantasy league data into PostgreSQL.")
    )

    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "Import player-week data for every completed week. "
            "Without this option, only the two newest completed "
            "weeks are refreshed."
        ),
    )

    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    settings = get_settings()

    mode = "full" if arguments.full else "incremental"

    print("=== ESPN DATABASE SYNC ===")
    print(f"League ID: {settings.espn_league_id}")
    print(f"Season: {settings.espn_season}")
    print(f"Mode: {mode}")
    print("Credentials intentionally hidden.")

    with SessionFactory() as session:
        result = synchronize_active_provider(
            session=session,
            settings=settings,
            full_sync=arguments.full,
        )

    print()
    print("=== SYNC RESULT ===")
    print(f"League ID: {result.league_id}")
    print(f"Season: {result.season}")
    print(f"Current week: {result.current_week}")
    print(f"Full sync: {result.full_sync}")
    print(f"Player weeks: {list(result.player_weeks)}")
    print(f"Teams upserted: {result.teams_upserted}")
    print(f"Matchups upserted: {result.matchups_upserted}")
    print(f"Fantasy byes upserted: {result.fantasy_byes_upserted}")
    print(f"Player-week scores upserted: {result.player_week_scores_upserted}")
    print("Synchronization completed successfully.")


if __name__ == "__main__":
    main()
