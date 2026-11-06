from collections.abc import Iterable
from typing import Any

from app.core.settings import get_settings
from app.espn.client import create_espn_league


def display_value(value: Any, fallback: str = "Unavailable") -> Any:
    return fallback if value is None else value


def print_divider(title: str) -> None:
    print()
    print(f"=== {title} ===")


def print_teams(teams: Iterable[Any]) -> None:
    print_divider("TEAMS")

    for team in teams:
        team_name = display_value(getattr(team, "team_name", None), "Unnamed team")
        wins = display_value(getattr(team, "wins", None), 0)
        losses = display_value(getattr(team, "losses", None), 0)
        ties = display_value(getattr(team, "ties", None), 0)
        points_for = display_value(getattr(team, "points_for", None), 0.0)
        points_against = display_value(getattr(team, "points_against", None), 0.0)

        print(
            f"{team_name}: "
            f"{wins}-{losses}-{ties}, "
            f"PF {float(points_for):.2f}, "
            f"PA {float(points_against):.2f}"
        )


def print_matchup_preview(league: Any) -> None:
    print_divider("MATCHUP PREVIEW")

    current_week = getattr(league, "current_week", 1)
    preview_week = max(1, current_week - 1)

    try:
        matchups = league.box_scores(week=preview_week)
    except Exception as exc:
        print(
            f"League access succeeded, but matchup week {preview_week} "
            f"could not be retrieved: {type(exc).__name__}"
        )
        return

    if not matchups:
        print(f"No matchups were returned for week {preview_week}.")
        return

    print(f"Showing up to three matchups from week {preview_week}:")

    for matchup in matchups[:3]:
        home_team = display_value(
            getattr(getattr(matchup, "home_team", None), "team_name", None),
            "Unknown home team",
        )
        away_team = display_value(
            getattr(getattr(matchup, "away_team", None), "team_name", None),
            "Unknown away team",
        )
        home_score = display_value(getattr(matchup, "home_score", None), 0.0)
        away_score = display_value(getattr(matchup, "away_score", None), 0.0)

        print(f"{away_team} {float(away_score):.2f} at {home_team} {float(home_score):.2f}")


def main() -> None:
    print("Loading local configuration...")

    settings = get_settings()

    print(
        f"Connecting to ESPN league {settings.espn_league_id} for season {settings.espn_season}..."
    )

    try:
        league = create_espn_league(settings)
    except Exception as exc:
        print_divider("CONNECTION FAILED")
        print(f"Error type: {type(exc).__name__}")
        print("Your ESPN credentials were not printed.")
        print()
        print("Likely causes:")
        print("1. ESPN_S2 or ESPN_SWID is missing, invalid, or expired.")
        print("2. The signed-in ESPN account cannot access this private league.")
        print("3. The 2026 league has not been activated or initialized.")
        print("4. ESPN changed an undocumented response used by espn-api.")
        raise SystemExit(1) from None

    print_divider("LEAGUE")
    print(f"Name: {display_value(getattr(league.settings, 'name', None))}")
    print(f"League ID: {settings.espn_league_id}")
    print(f"Season: {settings.espn_season}")
    print(f"Teams returned: {len(league.teams)}")
    print(f"Current week: {display_value(getattr(league, 'current_week', None))}")
    print(
        "Regular-season matchup periods: "
        f"{display_value(getattr(league.settings, 'reg_season_count', None))}"
    )

    print_teams(league.teams)
    print_matchup_preview(league)

    print_divider("RESULT")
    print("ESPN league access succeeded.")
    print("Credential values were not displayed.")
    print("The backend is ready for league-data inspection.")


if __name__ == "__main__":
    main()
