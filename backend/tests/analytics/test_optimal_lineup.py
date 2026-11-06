from app.analytics.optimal_lineup import (
    calculate_optimal_lineup_summaries,
    calculate_team_week_optimal_lineup,
)
from app.domain.lineup import (
    PlayerWeekSnapshot,
    TeamWeekRoster,
)


def create_player(
    player_id: int,
    name: str,
    position: str,
    actual_slot: str,
    eligible_slots: tuple[str, ...],
    points: float,
    *,
    is_starter: bool = False,
    is_bench: bool = False,
    is_ir: bool = False,
) -> PlayerWeekSnapshot:
    return PlayerWeekSnapshot(
        player_id=player_id,
        player_name=name,
        pro_team="NFL",
        default_position=position,
        actual_lineup_slot=actual_slot,
        eligible_lineup_slots=eligible_slots,
        points=points,
        projected_points=None,
        is_starter=is_starter,
        is_bench=is_bench,
        is_injured_reserve=is_ir,
        is_nfl_bye=False,
    )


def create_complete_roster() -> TeamWeekRoster:
    return TeamWeekRoster(
        team_id=1,
        team_name="Team Alpha",
        week=1,
        players=(
            create_player(
                1,
                "Starting QB",
                "QB",
                "QB",
                ("QB", "BE"),
                12.0,
                is_starter=True,
            ),
            create_player(
                2,
                "Bench QB",
                "QB",
                "BE",
                ("QB", "BE"),
                22.0,
                is_bench=True,
            ),
            create_player(
                3,
                "Starting RB One",
                "RB",
                "RB",
                ("RB", "RB/WR/TE", "BE"),
                10.0,
                is_starter=True,
            ),
            create_player(
                4,
                "Starting RB Two",
                "RB",
                "RB",
                ("RB", "RB/WR/TE", "BE"),
                9.0,
                is_starter=True,
            ),
            create_player(
                5,
                "Bench RB",
                "RB",
                "BE",
                ("RB", "RB/WR/TE", "BE"),
                18.0,
                is_bench=True,
            ),
            create_player(
                6,
                "Starting WR One",
                "WR",
                "WR",
                ("WR", "RB/WR/TE", "BE"),
                15.0,
                is_starter=True,
            ),
            create_player(
                7,
                "Starting WR Two",
                "WR",
                "WR",
                ("WR", "RB/WR/TE", "BE"),
                14.0,
                is_starter=True,
            ),
            create_player(
                8,
                "Bench WR",
                "WR",
                "BE",
                ("WR", "RB/WR/TE", "BE"),
                20.0,
                is_bench=True,
            ),
            create_player(
                9,
                "Starting TE",
                "TE",
                "TE",
                ("TE", "RB/WR/TE", "BE"),
                8.0,
                is_starter=True,
            ),
            create_player(
                10,
                "Starting Flex",
                "RB",
                "RB/WR/TE",
                ("RB", "RB/WR/TE", "BE"),
                7.0,
                is_starter=True,
            ),
            create_player(
                11,
                "Starting Kicker",
                "K",
                "K",
                ("K", "BE"),
                6.0,
                is_starter=True,
            ),
            create_player(
                12,
                "Starting Defense",
                "D/ST",
                "D/ST",
                ("D/ST", "BE"),
                5.0,
                is_starter=True,
            ),
        ),
    )


def test_optimizer_selects_highest_legal_lineup() -> None:
    result = calculate_team_week_optimal_lineup(create_complete_roster())

    assert result.actual_points == 86.0
    assert result.optimal_points == 118.0
    assert result.missed_points == 32.0
    assert result.efficiency_percentage == 72.88
    assert result.actual_starter_count == 9
    assert result.optimal_filled_slot_count == 9
    assert result.unfilled_slots == ()

    optimal_names = {player.player_name for player in result.optimal_lineup}

    assert "Bench QB" in optimal_names
    assert "Bench RB" in optimal_names
    assert "Bench WR" in optimal_names


def test_optimizer_does_not_use_ir_player() -> None:
    roster = create_complete_roster()

    ir_player = create_player(
        99,
        "IR Superstar",
        "RB",
        "IR",
        ("RB", "RB/WR/TE", "BE", "IR"),
        50.0,
        is_ir=True,
    )

    roster = TeamWeekRoster(
        team_id=roster.team_id,
        team_name=roster.team_name,
        week=roster.week,
        players=roster.players + (ir_player,),
    )

    result = calculate_team_week_optimal_lineup(roster)

    assert all(player.player_id != 99 for player in result.optimal_lineup)


def test_optimizer_leaves_missing_kicker_unfilled() -> None:
    roster = create_complete_roster()

    roster_without_kicker = TeamWeekRoster(
        team_id=roster.team_id,
        team_name=roster.team_name,
        week=roster.week,
        players=tuple(player for player in roster.players if player.default_position != "K"),
    )

    result = calculate_team_week_optimal_lineup(roster_without_kicker)

    assert result.actual_starter_count == 8
    assert result.optimal_filled_slot_count == 8
    assert result.unfilled_slots == ("K",)

    kicker = next(player for player in result.optimal_lineup if player.lineup_slot == "K")

    assert kicker.is_unfilled is True
    assert kicker.player_id is None
    assert kicker.points == 0.0


def test_optimizer_uses_bench_kicker_for_empty_slot() -> None:
    roster = create_complete_roster()

    players_without_starting_kicker = tuple(
        player for player in roster.players if player.default_position != "K"
    )

    bench_kicker = create_player(
        50,
        "Bench Kicker",
        "K",
        "BE",
        ("K", "BE"),
        11.0,
        is_bench=True,
    )

    roster = TeamWeekRoster(
        team_id=roster.team_id,
        team_name=roster.team_name,
        week=roster.week,
        players=(players_without_starting_kicker + (bench_kicker,)),
    )

    result = calculate_team_week_optimal_lineup(roster)

    kicker = next(player for player in result.optimal_lineup if player.lineup_slot == "K")

    assert result.actual_starter_count == 8
    assert result.optimal_filled_slot_count == 9
    assert result.unfilled_slots == ()
    assert kicker.player_id == 50
    assert kicker.player_name == "Bench Kicker"
    assert kicker.points == 11.0


def test_optimizer_never_assigns_player_twice() -> None:
    result = calculate_team_week_optimal_lineup(create_complete_roster())

    player_ids = [
        player.player_id for player in result.optimal_lineup if player.player_id is not None
    ]

    assert len(player_ids) == len(set(player_ids))


def test_summary_combines_multiple_weeks() -> None:
    first_roster = create_complete_roster()
    second_roster = TeamWeekRoster(
        team_id=first_roster.team_id,
        team_name=first_roster.team_name,
        week=2,
        players=first_roster.players,
    )

    summaries = calculate_optimal_lineup_summaries((second_roster, first_roster))

    assert len(summaries) == 1

    summary = summaries[0]

    assert summary.team_id == 1
    assert summary.weeks_played == 2
    assert summary.actual_points == 172.0
    assert summary.optimal_points == 236.0
    assert summary.missed_points == 64.0
    assert summary.efficiency_percentage == 72.88
    assert [result.week for result in summary.weekly_results] == [1, 2]
