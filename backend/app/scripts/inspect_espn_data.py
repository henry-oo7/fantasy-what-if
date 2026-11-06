from typing import Any

from app.core.settings import get_settings
from app.espn.client import create_espn_league


def public_data_attributes(value: Any):
    return sorted(
        name
        for name in dir(value)
        if not name.startswith("_") and not callable(getattr(value, name, None))
    )


def public_methods(value: Any):
    return sorted(
        name
        for name in dir(value)
        if not name.startswith("_") and callable(getattr(value, name, None))
    )


def value_or_unavailable(value: Any):
    return "Unavailable" if value is None else value


def print_heading(title: str):
    print()
    print(f"=== {title} ===")


def inspect_league(league: Any):
    print_heading("LEAGUE OBJECT")

    attributes = public_data_attributes(league)
    methods = public_methods(league)

    print(f"Public data attributes ({len(attributes)}):")
    print(", ".join(attributes))

    print()
    print(f"Public methods ({len(methods)}):")
    print(", ".join(methods))


def inspect_settings(league: Any):
    print_heading("LEAGUE SETTINGS")

    settings = league.settings
    attributes = public_data_attributes(settings)

    print(f"Object type: {type(settings).__name__}")
    print(f"Public attributes ({len(attributes)}):")
    print(", ".join(attributes))

    selected_attributes = [
        "name",
        "reg_season_count",
        "playoff_team_count",
        "scoring_type",
        "team_count",
        "trade_deadline",
        "waiver_type",
    ]

    print()
    print("Selected settings:")

    for attribute_name in selected_attributes:
        value = getattr(settings, attribute_name, None)
        print(f"{attribute_name}: {value_or_unavailable(value)}")


def inspect_teams(league: Any):
    print_heading("TEAMS")

    if not league.teams:
        print("No teams returned.")
        return

    first_team = league.teams[0]
    attributes = public_data_attributes(first_team)

    print(f"Team object type: {type(first_team).__name__}")
    print(f"Public team attributes ({len(attributes)}):")
    print(", ".join(attributes))

    print()
    print("Team roster counts:")

    for team in league.teams:
        team_id = value_or_unavailable(getattr(team, "team_id", None))
        team_name = value_or_unavailable(getattr(team, "team_name", None))
        roster = getattr(team, "roster", [])

        print(f"Team ID {team_id}: {team_name}, current roster entries: {len(roster)}")


def inspect_current_rosters(league: Any):
    print_heading("CURRENT ROSTERS")

    if not league.teams:
        print("No teams returned.")
        return

    first_team = league.teams[0]
    roster = getattr(first_team, "roster", [])

    print(f"Team inspected: {first_team.team_name}")
    print(f"Roster entries: {len(roster)}")

    if not roster:
        print("No current roster entries were returned.")
        return

    first_player = roster[0]
    attributes = public_data_attributes(first_player)

    print(f"Player object type: {type(first_player).__name__}")
    print(f"Public player attributes ({len(attributes)}):")
    print(", ".join(attributes))

    print()
    print("First five roster entries:")

    for player in roster[:5]:
        player_id = value_or_unavailable(getattr(player, "playerId", None))
        name = value_or_unavailable(getattr(player, "name", None))
        position = value_or_unavailable(getattr(player, "position", None))
        lineup_slot = value_or_unavailable(getattr(player, "lineupSlot", None))
        total_points = value_or_unavailable(getattr(player, "total_points", None))

        print(
            f"Player ID {player_id}: {name}, "
            f"position {position}, slot {lineup_slot}, "
            f"season points {total_points}"
        )


def inspect_scoreboard(league: Any):
    print_heading("WEEKLY SCOREBOARD")

    current_week = int(getattr(league, "current_week", 1))
    week = max(1, current_week - 1)
    matchups = league.scoreboard(week=week)

    print(f"Week inspected: {week}")
    print(f"Matchups returned: {len(matchups)}")

    if not matchups:
        return

    first_matchup = matchups[0]
    attributes = public_data_attributes(first_matchup)

    print(f"Matchup object type: {type(first_matchup).__name__}")
    print(f"Public matchup attributes ({len(attributes)}):")
    print(", ".join(attributes))

    print()
    print("First three matchups:")

    for matchup in matchups[:3]:
        home_team = getattr(matchup, "home_team", None)
        away_team = getattr(matchup, "away_team", None)

        home_name = value_or_unavailable(getattr(home_team, "team_name", None))
        away_name = value_or_unavailable(getattr(away_team, "team_name", None))
        home_score = value_or_unavailable(getattr(matchup, "home_score", None))
        away_score = value_or_unavailable(getattr(matchup, "away_score", None))

        print(f"{away_name} {away_score} at {home_name} {home_score}")


def inspect_box_scores(league: Any):
    print_heading("WEEKLY BOX SCORES")

    current_week = int(getattr(league, "current_week", 1))
    week = max(1, current_week - 1)
    box_scores = league.box_scores(week=week)

    print(f"Week inspected: {week}")
    print(f"Box scores returned: {len(box_scores)}")

    if not box_scores:
        return

    first_box_score = box_scores[0]
    attributes = public_data_attributes(first_box_score)

    print(f"Box score object type: {type(first_box_score).__name__}")
    print(f"Public box score attributes ({len(attributes)}):")
    print(", ".join(attributes))

    home_lineup = getattr(first_box_score, "home_lineup", [])
    away_lineup = getattr(first_box_score, "away_lineup", [])

    print(f"First home lineup entries: {len(home_lineup)}")
    print(f"First away lineup entries: {len(away_lineup)}")

    sample_lineup = home_lineup or away_lineup

    if not sample_lineup:
        return

    player_attributes = public_data_attributes(sample_lineup[0])

    print()
    print(f"Box-score player attributes ({len(player_attributes)}):")
    print(", ".join(player_attributes))

    print()
    print("First five box-score players:")

    for player in sample_lineup[:5]:
        player_id = value_or_unavailable(getattr(player, "playerId", None))
        name = value_or_unavailable(getattr(player, "name", None))
        position = value_or_unavailable(getattr(player, "position", None))
        lineup_slot = value_or_unavailable(getattr(player, "lineupSlot", None))
        points = value_or_unavailable(getattr(player, "points", None))
        projected = value_or_unavailable(getattr(player, "projected_points", None))

        print(
            f"Player ID {player_id}: {name}, "
            f"position {position}, slot {lineup_slot}, "
            f"points {points}, projected {projected}"
        )


def inspect_draft(league: Any):
    print_heading("DRAFT")

    draft = getattr(league, "draft", None)

    if draft is None:
        print("The league object has no draft value.")
        return

    print(f"Draft entries: {len(draft)}")

    if not draft:
        print("No draft selections were returned.")
        return

    first_pick = draft[0]
    attributes = public_data_attributes(first_pick)

    print(f"Draft pick object type: {type(first_pick).__name__}")
    print(f"Public draft attributes ({len(attributes)}):")
    print(", ".join(attributes))

    print()
    print("First five draft selections:")

    for pick in draft[:5]:
        player_name = value_or_unavailable(getattr(pick, "playerName", None))
        player_id = value_or_unavailable(getattr(pick, "playerId", None))
        team = getattr(pick, "team", None)
        team_name = value_or_unavailable(getattr(team, "team_name", None))
        round_number = value_or_unavailable(getattr(pick, "round_num", None))
        round_pick = value_or_unavailable(getattr(pick, "round_pick", None))

        print(
            f"Round {round_number}, pick {round_pick}: {player_name} ({player_id}) to {team_name}"
        )


def inspect_recent_activity(league: Any):
    print_heading("RECENT ACTIVITY")

    recent_activity = getattr(league, "recent_activity", None)

    if recent_activity is None or not callable(recent_activity):
        print("The league does not expose recent_activity().")
        return

    activities = recent_activity(size=25)

    print(f"Activity entries returned: {len(activities)}")

    if not activities:
        print("No recent activity entries were returned.")
        return

    first_activity = activities[0]
    attributes = public_data_attributes(first_activity)

    print(f"Activity object type: {type(first_activity).__name__}")
    print(f"Public activity attributes ({len(attributes)}):")
    print(", ".join(attributes))

    print()
    print("First five activities:")

    for activity in activities[:5]:
        activity_type = value_or_unavailable(getattr(activity, "type", None))
        activity_date = value_or_unavailable(getattr(activity, "date", None))
        actions = getattr(activity, "actions", [])

        print(f"Type {activity_type}, date {activity_date}, actions {len(actions)}")


def run_section(name: str, inspection, league: Any):
    try:
        inspection(league)
        return True
    except Exception as exc:
        print_heading(f"{name}: UNAVAILABLE")
        print(f"Error type: {type(exc).__name__}")
        print(f"Safe error message: {str(exc)[:200]}")
        return False


def main():
    settings = get_settings()

    print("Connecting to ESPN...")
    league = create_espn_league(settings)

    print_heading("CONNECTION")
    print(f"League: {league.settings.name}")
    print(f"League ID: {settings.espn_league_id}")
    print(f"Season: {settings.espn_season}")
    print(f"Current week: {league.current_week}")
    print(f"Teams: {len(league.teams)}")
    print("Credentials intentionally hidden.")

    inspections = [
        ("LEAGUE OBJECT", inspect_league),
        ("LEAGUE SETTINGS", inspect_settings),
        ("TEAMS", inspect_teams),
        ("CURRENT ROSTERS", inspect_current_rosters),
        ("WEEKLY SCOREBOARD", inspect_scoreboard),
        ("WEEKLY BOX SCORES", inspect_box_scores),
        ("DRAFT", inspect_draft),
        ("RECENT ACTIVITY", inspect_recent_activity),
    ]

    successful = 0

    for name, inspection in inspections:
        if run_section(name, inspection, league):
            successful += 1

    print_heading("INSPECTION SUMMARY")
    print(f"Successful sections: {successful}")
    print(f"Unavailable sections: {len(inspections) - successful}")
    print("This inspection was read-only.")
    print("No credentials were displayed.")
    print("No ESPN data was changed.")


if __name__ == "__main__":
    main()
