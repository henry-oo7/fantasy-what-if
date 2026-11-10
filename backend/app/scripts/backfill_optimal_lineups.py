"""Backfill persisted optimal-lineup results from stored rosters."""

import argparse

from app.analytics.optimal_lineup import (
    calculate_team_week_optimal_lineup,
)
from app.db.session import SessionFactory
from app.repositories.optimal_lineup_repository import (
    replace_optimal_lineup_results,
)
from app.repositories.player_week_repository import (
    load_team_week_rosters,
)
from app.services.active_season_context import (
    resolve_active_season_context,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Rebuild persisted optimal-lineup results from canonical player-week data.")
    )

    parser.add_argument(
        "--season-id",
        type=int,
        help=("Internal season ID. Defaults to the installation's active season."),
    )

    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()

    with SessionFactory() as session:
        season_id = arguments.season_id

        if season_id is None:
            season_id = resolve_active_season_context(
                session=session,
            ).season_id

        rosters = load_team_week_rosters(
            session,
            season_id=season_id,
        )

        if not rosters:
            raise SystemExit(f"No stored team-week rosters were found for season {season_id}.")

        weeks = tuple(sorted({roster.week for roster in rosters}))

        results = tuple(calculate_team_week_optimal_lineup(roster) for roster in rosters)

        stored = replace_optimal_lineup_results(
            session,
            season_id=season_id,
            weeks=weeks,
            results=results,
        )

        session.commit()

    print("=== OPTIMAL LINEUP BACKFILL COMPLETE ===")
    print(f"Season ID: {season_id}")
    print(f"Weeks rebuilt: {list(weeks)}")
    print(f"Team-week rosters: {len(rosters)}")
    print(f"Results stored: {len(stored)}")


if __name__ == "__main__":
    main()
