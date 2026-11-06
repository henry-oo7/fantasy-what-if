from app.analytics.league_average import (
    calculate_league_average_standings,
)
from app.domain.league import (
    LeagueSnapshot,
    MatchupSnapshot,
    TeamSnapshot,
)


def create_team(
    team_id: int,
    name: str,
) -> TeamSnapshot:
    return TeamSnapshot(
        team_id=team_id,
        name=name,
        wins=0,
        losses=0,
        ties=0,
        points_for=0.0,
        points_against=0.0,
    )


def create_snapshot() -> LeagueSnapshot:
    return LeagueSnapshot(
        league_id=529250970,
        name="Test League",
        season=2026,
        current_week=2,
        regular_season_weeks=12,
        teams=(
            create_team(1, "Team Alpha"),
            create_team(2, "Team Bravo"),
            create_team(3, "Team Charlie"),
            create_team(4, "Team Delta"),
        ),
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=110.0,
            ),
            MatchupSnapshot(
                week=1,
                home_team_id=3,
                away_team_id=4,
                home_score=100.0,
                away_score=90.0,
            ),
        ),
    )


def test_league_average_adds_second_weekly_decision() -> None:
    standings = calculate_league_average_standings(create_snapshot())

    by_team_id = {standing.team_id: standing for standing in standings}

    alpha = by_team_id[1]
    bravo = by_team_id[2]
    charlie = by_team_id[3]
    delta = by_team_id[4]

    assert alpha.actual_record.wins == 1
    assert alpha.average_record.wins == 1
    assert alpha.combined_record.wins == 2
    assert alpha.combined_record.losses == 0

    assert bravo.actual_record.losses == 1
    assert bravo.average_record.wins == 1
    assert bravo.combined_record.wins == 1
    assert bravo.combined_record.losses == 1

    assert charlie.actual_record.wins == 1
    assert charlie.average_record.losses == 1
    assert charlie.combined_record.wins == 1
    assert charlie.combined_record.losses == 1

    assert delta.actual_record.losses == 1
    assert delta.average_record.losses == 1
    assert delta.combined_record.wins == 0
    assert delta.combined_record.losses == 2


def test_league_average_uses_shared_weekly_threshold() -> None:
    standings = calculate_league_average_standings(create_snapshot())

    by_team_id = {standing.team_id: standing for standing in standings}

    for standing in standings:
        assert standing.weekly_results[0].league_average == 105.0

    assert by_team_id[1].weekly_results[0].average_result == "win"
    assert by_team_id[2].weekly_results[0].average_result == "win"
    assert by_team_id[3].weekly_results[0].average_result == "loss"
    assert by_team_id[4].weekly_results[0].average_result == "loss"


def test_league_average_ranks_combined_records() -> None:
    standings = calculate_league_average_standings(create_snapshot())

    assert [standing.team_name for standing in standings] == [
        "Team Alpha",
        "Team Bravo",
        "Team Charlie",
        "Team Delta",
    ]

    assert [standing.rank for standing in standings] == [1, 2, 3, 4]


def test_league_average_excludes_current_week() -> None:
    snapshot = LeagueSnapshot(
        league_id=529250970,
        name="Test League",
        season=2026,
        current_week=1,
        regular_season_weeks=12,
        teams=(
            create_team(1, "Team Alpha"),
            create_team(2, "Team Bravo"),
        ),
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=120.0,
                away_score=110.0,
            ),
        ),
    )

    standings = calculate_league_average_standings(snapshot)

    assert len(standings) == 2
    assert all(standing.weeks_played == 0 for standing in standings)
    assert all(standing.combined_record.wins == 0 for standing in standings)
    assert all(standing.combined_record.losses == 0 for standing in standings)


def test_league_average_counts_exact_average_as_tie() -> None:
    snapshot = LeagueSnapshot(
        league_id=529250970,
        name="Test League",
        season=2026,
        current_week=2,
        regular_season_weeks=12,
        teams=(
            create_team(1, "Team Alpha"),
            create_team(2, "Team Bravo"),
        ),
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=100.0,
                away_score=100.0,
            ),
        ),
    )

    standings = calculate_league_average_standings(snapshot)

    for standing in standings:
        assert standing.actual_record.ties == 1
        assert standing.average_record.ties == 1
        assert standing.combined_record.ties == 2
        assert standing.combined_record.win_percentage == 0.5
