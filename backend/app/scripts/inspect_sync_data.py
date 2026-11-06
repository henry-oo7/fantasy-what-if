from collections import Counter
from typing import Any

from app.core.settings import get_settings
from app.espn.client import create_espn_league


def safe_value(value: Any) -> str:
    if value is None:
        return "None"

    return str(value)


def team_name(team: Any) -> str:
    if team is None:
        return "None"

    return safe_value(getattr(team, "team_name", None))


def public_attributes(value: Any) -> list[str]:
    if value is None:
        return []

    return sorted(
        name
        for name in dir(value)
        if not name.startswith("_") and not callable(getattr(value, name, None))
    )


def main() -> None:
    settings = get_settings()
    league = create_espn_league(settings)

    current_week = int(league.current_week)
    regular_season_weeks = int(league.settings.reg_season_count)

    print("=== LEAGUE ===")
    print(f"Name: {league.settings.name}")
    print(f"League ID: {settings.espn_league_id}")
    print(f"Season: {settings.espn_season}")
    print(f"Current week: {current_week}")
    print(f"Regular-season weeks: {regular_season_weeks}")
    print(f"Teams: {len(league.teams)}")
    print("Credentials intentionally hidden.")

    print()
    print("=== WEEK-BY-WEEK SCHEDULE SHAPE ===")

    weeks_with_missing_teams: list[int] = []
    weeks_with_zero_matchups: list[int] = []
    week_team_appearances: dict[int, Counter[int]] = {}
    box_scores_by_week: dict[int, list[Any]] = {}

    for week in range(1, regular_season_weeks + 1):
        try:
            box_scores = list(league.box_scores(week=week))
        except Exception as exc:
            print(f"Week {week}: ERROR {type(exc).__name__}: {str(exc)[:120]}")
            continue

        box_scores_by_week[week] = box_scores

        if not box_scores:
            weeks_with_zero_matchups.append(week)

        appearances: Counter[int] = Counter()
        missing_team_count = 0
        zero_to_zero_count = 0

        for matchup in box_scores:
            home_team = getattr(matchup, "home_team", None)
            away_team = getattr(matchup, "away_team", None)

            if home_team is None or away_team is None:
                missing_team_count += 1

            home_team_id = getattr(home_team, "team_id", None)
            away_team_id = getattr(away_team, "team_id", None)

            if home_team_id is not None:
                appearances[int(home_team_id)] += 1

            if away_team_id is not None:
                appearances[int(away_team_id)] += 1

            home_score = getattr(matchup, "home_score", None)
            away_score = getattr(matchup, "away_score", None)

            if home_score == 0 and away_score == 0:
                zero_to_zero_count += 1

        week_team_appearances[week] = appearances

        missing_team_ids = sorted(
            team.team_id for team in league.teams if appearances[team.team_id] == 0
        )

        duplicate_team_ids = sorted(
            current_team_id for current_team_id, count in appearances.items() if count > 1
        )

        if missing_team_count:
            weeks_with_missing_teams.append(week)

        state = (
            "COMPLETED" if week < current_week else "CURRENT" if week == current_week else "FUTURE"
        )

        print(
            f"Week {week}: state={state}, "
            f"box_scores={len(box_scores)}, "
            f"teams_seen={len(appearances)}, "
            f"missing_team_objects={missing_team_count}, "
            f"zero_to_zero_matchups={zero_to_zero_count}, "
            f"teams_without_matchup={missing_team_ids}, "
            f"duplicate_team_ids={duplicate_team_ids}"
        )

    print()
    print("=== POSSIBLE FANTASY BYE WEEKS ===")

    fantasy_byes_found = False

    for week, appearances in week_team_appearances.items():
        missing_teams = [team for team in league.teams if appearances[team.team_id] == 0]

        for team in missing_teams:
            fantasy_byes_found = True
            print(f"Week {week}: team_id={team.team_id}, team_name={team.team_name}")

    if not fantasy_byes_found:
        print("No team was absent from a returned regular-season week.")

    print()
    print("=== FIRST COMPLETED BOX SCORE OBJECT ===")

    completed_box_score = None
    completed_box_score_week = None

    for week in range(1, min(current_week, regular_season_weeks + 1)):
        box_scores = box_scores_by_week.get(week, [])

        if box_scores:
            completed_box_score = box_scores[0]
            completed_box_score_week = week
            break

    if completed_box_score is None:
        print("No completed box score was found.")
        return

    print(f"Week: {completed_box_score_week}")
    print(f"Object type: {type(completed_box_score).__name__}")
    print("Attributes: " + ", ".join(public_attributes(completed_box_score)))
    print(f"Home team: {team_name(completed_box_score.home_team)}")
    print(f"Away team: {team_name(completed_box_score.away_team)}")
    print(f"Home lineup entries: {len(getattr(completed_box_score, 'home_lineup', []))}")
    print(f"Away lineup entries: {len(getattr(completed_box_score, 'away_lineup', []))}")

    sample_lineup = getattr(completed_box_score, "home_lineup", []) or getattr(
        completed_box_score, "away_lineup", []
    )

    if not sample_lineup:
        print()
        print("=== PLAYER DATA ===")
        print("No lineup players were returned.")
        return

    print()
    print("=== PLAYER OBJECT SHAPE ===")

    sample_player = sample_lineup[0]

    print(f"Object type: {type(sample_player).__name__}")
    print("Attributes: " + ", ".join(public_attributes(sample_player)))

    print()
    print("=== SAMPLE WEEKLY PLAYERS ===")

    tracked_attributes = [
        "playerId",
        "name",
        "position",
        "proTeam",
        "lineupSlot",
        "eligibleSlots",
        "points",
        "projected_points",
        "total_points",
        "injuryStatus",
        "active_status",
    ]

    for player in sample_lineup[:10]:
        print("---")

        for attribute_name in tracked_attributes:
            value = getattr(player, attribute_name, None)
            print(f"{attribute_name}: {safe_value(value)}")

    print()
    print("=== LINEUP SLOT DISTRIBUTION ===")

    lineup_slot_counts: Counter[str] = Counter()
    position_counts: Counter[str] = Counter()
    eligible_slot_examples: dict[str, str] = {}

    completed_player_count = 0

    for week in range(1, min(current_week, regular_season_weeks + 1)):
        for box_score in box_scores_by_week.get(week, []):
            lineups = [
                getattr(box_score, "home_lineup", []),
                getattr(box_score, "away_lineup", []),
            ]

            for lineup in lineups:
                for player in lineup:
                    completed_player_count += 1

                    lineup_slot = safe_value(getattr(player, "lineupSlot", None))
                    position = safe_value(getattr(player, "position", None))
                    eligible_slots = safe_value(getattr(player, "eligibleSlots", None))

                    lineup_slot_counts[lineup_slot] += 1
                    position_counts[position] += 1
                    eligible_slot_examples.setdefault(
                        position,
                        eligible_slots,
                    )

    print(f"Completed player-week entries observed: {completed_player_count}")

    print("Lineup slots:")
    for slot, count in sorted(lineup_slot_counts.items()):
        print(f"  {slot}: {count}")

    print("Default positions:")
    for position, count in sorted(position_counts.items()):
        print(f"  {position}: {count}")

    print("Eligible-slot examples by position:")
    for position, slots in sorted(eligible_slot_examples.items()):
        print(f"  {position}: {slots}")

    print()
    print("=== INSPECTION SUMMARY ===")
    print(f"Weeks with no box scores: {weeks_with_zero_matchups}")
    print(f"Weeks with missing team objects: {weeks_with_missing_teams}")
    print("Inspection was read-only.")
    print("No credentials were displayed.")
    print("No database rows were written.")


if __name__ == "__main__":
    main()
