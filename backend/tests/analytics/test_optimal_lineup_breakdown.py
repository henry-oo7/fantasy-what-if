from app.analytics.optimal_lineup import (
    calculate_optimal_lineup_summaries,
)
from app.analytics.optimal_lineup_breakdown import (
    build_optimal_lineup_breakdown,
    calculate_result,
)
from app.domain.league import (
    LeagueSnapshot,
    MatchupSnapshot,
    TeamSnapshot,
)
from app.domain.lineup import (
    PlayerWeekSnapshot,
    TeamWeekRoster,
)


def player(
    player_id: int,
    *,
    name: str,
    slot: str,
    points: float,
    eligible: tuple[str, ...],
    starter: bool,
) -> PlayerWeekSnapshot:
    return PlayerWeekSnapshot(
        player_id=player_id,
        player_name=name,
        pro_team="NFL",
        default_position=eligible[0],
        actual_lineup_slot=slot,
        eligible_lineup_slots=eligible,
        points=points,
        projected_points=None,
        is_starter=starter,
        is_bench=not starter,
        is_injured_reserve=False,
        is_nfl_bye=False,
    )


def summary():
    roster = TeamWeekRoster(
        team_id=1,
        team_name="Team Alpha",
        week=1,
        players=(
            player(
                1,
                name="Starter",
                slot="QB",
                points=86.0,
                eligible=("QB", "BENCH"),
                starter=True,
            ),
            player(
                2,
                name="Bench Player",
                slot="BENCH",
                points=96.0,
                eligible=("QB", "BENCH"),
                starter=False,
            ),
        ),
    )

    return calculate_optimal_lineup_summaries((roster,))[0]


def snapshot() -> LeagueSnapshot:
    return LeagueSnapshot(
        league_id=1,
        name="Test League",
        season=2026,
        current_week=2,
        regular_season_weeks=14,
        teams=(
            TeamSnapshot(
                team_id=1,
                name="Team Alpha",
                wins=0,
                losses=1,
                ties=0,
                points_for=86.0,
                points_against=90.0,
            ),
            TeamSnapshot(
                team_id=2,
                name="Team Bravo",
                wins=1,
                losses=0,
                ties=0,
                points_for=90.0,
                points_against=86.0,
            ),
        ),
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=1,
                away_team_id=2,
                home_score=86.0,
                away_score=90.0,
            ),
        ),
    )


def test_calculate_result() -> None:
    assert calculate_result(101.0, 100.0) == "win"
    assert calculate_result(99.0, 100.0) == "loss"
    assert calculate_result(100.0, 100.0) == "tie"


def test_breakdown_adds_matchup_context() -> None:
    breakdown = build_optimal_lineup_breakdown(
        summary=summary(),
        snapshot=snapshot(),
    )

    assert breakdown.actual_record.losses == 1
    assert breakdown.optimal_record.wins == 1
    assert breakdown.equivalent_wins_gained == 1.0
    assert breakdown.results_flipped == 1

    week = breakdown.weekly_results[0]

    assert week.opponent_team_id == 2
    assert week.opponent_team_name == "Team Bravo"
    assert week.opponent_points == 90.0
    assert week.actual_result == "loss"
    assert week.optimal_result == "win"
    assert week.result_flipped is True


def test_breakdown_supports_away_team() -> None:
    league = snapshot()
    away_summary = summary()

    reversed_matchup = LeagueSnapshot(
        league_id=league.league_id,
        name=league.name,
        season=league.season,
        current_week=league.current_week,
        regular_season_weeks=(league.regular_season_weeks),
        teams=league.teams,
        matchups=(
            MatchupSnapshot(
                week=1,
                home_team_id=2,
                away_team_id=1,
                home_score=90.0,
                away_score=86.0,
            ),
        ),
    )

    breakdown = build_optimal_lineup_breakdown(
        summary=away_summary,
        snapshot=reversed_matchup,
    )

    assert breakdown.weekly_results[0].opponent_team_id == 2


def test_breakdown_rejects_missing_matchup() -> None:
    league = snapshot()

    empty_snapshot = LeagueSnapshot(
        league_id=league.league_id,
        name=league.name,
        season=league.season,
        current_week=league.current_week,
        regular_season_weeks=(league.regular_season_weeks),
        teams=league.teams,
        matchups=(),
    )

    try:
        build_optimal_lineup_breakdown(
            summary=summary(),
            snapshot=empty_snapshot,
        )
    except ValueError as exc:
        assert "does not contain a completed matchup" in str(exc)
    else:
        raise AssertionError("Expected missing matchup rejection.")
