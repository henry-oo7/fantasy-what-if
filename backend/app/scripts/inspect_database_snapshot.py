from app.core.settings import get_settings
from app.db.session import SessionFactory
from app.repositories.league_repository import load_league_snapshot


def main() -> None:
    settings = get_settings()

    with SessionFactory() as session:
        snapshot = load_league_snapshot(
            session,
            espn_league_id=settings.espn_league_id,
            year=settings.espn_season,
        )

    if snapshot is None:
        raise SystemExit("No synchronized league snapshot was found.")

    weeks = sorted({matchup.week for matchup in snapshot.matchups})

    print("=== DATABASE LEAGUE SNAPSHOT ===")
    print(f"League: {snapshot.name}")
    print(f"League ID: {snapshot.league_id}")
    print(f"Season: {snapshot.season}")
    print(f"Current week: {snapshot.current_week}")
    print(f"Regular-season weeks: {snapshot.regular_season_weeks}")
    print(f"Teams: {len(snapshot.teams)}")
    print(f"Completed matchups: {len(snapshot.matchups)}")
    print(f"Completed weeks: {weeks}")

    print()
    print("=== EXPECTED COUNTS ===")

    expected_completed_weeks = max(
        min(
            snapshot.current_week - 1,
            snapshot.regular_season_weeks,
        ),
        0,
    )

    expected_matchups = expected_completed_weeks * len(snapshot.teams) // 2

    print(f"Expected completed weeks: {expected_completed_weeks}")
    print(f"Expected completed matchups: {expected_matchups}")
    print(f"Completed matchup count valid: {len(snapshot.matchups) == expected_matchups}")

    print()
    print("=== TEAM RECORDS ===")

    for team in snapshot.teams:
        print(
            f"{team.name}: "
            f"{team.wins}-{team.losses}-{team.ties}, "
            f"PF {team.points_for:.2f}, "
            f"PA {team.points_against:.2f}"
        )

    print()
    print("Snapshot was loaded entirely from PostgreSQL.")
    print("No ESPN request was made.")


if __name__ == "__main__":
    main()
