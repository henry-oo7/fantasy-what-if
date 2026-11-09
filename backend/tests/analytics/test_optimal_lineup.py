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
                ("QB", "BENCH"),
                12.0,
                is_starter=True,
            ),
            create_player(
                2,
                "Bench QB",
                "QB",
                "BENCH",
                ("QB", "BENCH"),
                22.0,
                is_bench=True,
            ),
            create_player(
                3,
                "Starting RB One",
                "RB",
                "RB",
                ("RB", "FLEX", "BENCH"),
                10.0,
                is_starter=True,
            ),
            create_player(
                4,
                "Starting RB Two",
                "RB",
                "RB",
                ("RB", "FLEX", "BENCH"),
                9.0,
                is_starter=True,
            ),
            create_player(
                5,
                "Bench RB",
                "RB",
                "BENCH",
                ("RB", "FLEX", "BENCH"),
                18.0,
                is_bench=True,
            ),
            create_player(
                6,
                "Starting WR One",
                "WR",
                "WR",
                ("WR", "FLEX", "BENCH"),
                15.0,
                is_starter=True,
            ),
            create_player(
                7,
                "Starting WR Two",
                "WR",
                "WR",
                ("WR", "FLEX", "BENCH"),
                14.0,
                is_starter=True,
            ),
            create_player(
                8,
                "Bench WR",
                "WR",
                "BENCH",
                ("WR", "FLEX", "BENCH"),
                20.0,
                is_bench=True,
            ),
            create_player(
                9,
                "Starting TE",
                "TE",
                "TE",
                ("TE", "FLEX", "BENCH"),
                8.0,
                is_starter=True,
            ),
            create_player(
                10,
                "Starting Flex",
                "RB",
                "FLEX",
                ("RB", "FLEX", "BENCH"),
                7.0,
                is_starter=True,
            ),
            create_player(
                11,
                "Starting Kicker",
                "K",
                "K",
                ("K", "BENCH"),
                6.0,
                is_starter=True,
            ),
            create_player(
                12,
                "Starting Defense",
                "DST",
                "DST",
                ("DST", "BENCH"),
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
        ("RB", "FLEX", "BENCH", "IR"),
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
        "BENCH",
        ("K", "BENCH"),
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


def test_substitutions_ignore_retained_players_that_exchange_rb_and_flex() -> None:
    players = (
        create_player(
            201,
            "Starting QB",
            "QB",
            "QB",
            ("QB", "BENCH"),
            20.0,
            is_starter=True,
        ),
        create_player(
            202,
            "D'Andre Swift",
            "RB",
            "RB",
            ("RB", "FLEX", "BENCH"),
            10.0,
            is_starter=True,
        ),
        create_player(
            203,
            "Javonte Williams",
            "RB",
            "FLEX",
            ("RB", "FLEX", "BENCH"),
            14.5,
            is_starter=True,
        ),
        create_player(
            204,
            "Starting RB",
            "RB",
            "RB",
            ("RB", "FLEX", "BENCH"),
            12.0,
            is_starter=True,
        ),
        create_player(
            205,
            "Starting WR One",
            "WR",
            "WR",
            ("WR", "FLEX", "BENCH"),
            11.0,
            is_starter=True,
        ),
        create_player(
            206,
            "Devaughn Vele",
            "WR",
            "WR",
            ("WR", "FLEX", "BENCH"),
            5.0,
            is_starter=True,
        ),
        create_player(
            207,
            "Starting TE",
            "TE",
            "TE",
            ("TE", "FLEX", "BENCH"),
            8.0,
            is_starter=True,
        ),
        create_player(
            208,
            "Starting Kicker",
            "K",
            "K",
            ("K", "BENCH"),
            7.0,
            is_starter=True,
        ),
        create_player(
            209,
            "Starting Defense",
            "DST",
            "DST",
            ("DST", "BENCH"),
            6.0,
            is_starter=True,
        ),
        create_player(
            210,
            "KC Concepcion",
            "WR",
            "BENCH",
            ("WR", "FLEX", "BENCH"),
            10.3,
            is_bench=True,
        ),
    )

    result = calculate_team_week_optimal_lineup(
        TeamWeekRoster(
            team_id=1,
            team_name="Team Alpha",
            week=1,
            players=players,
        )
    )

    actual_ids = {
        player.player_id for player in result.actual_lineup if player.player_id is not None
    }
    optimal_ids = {
        player.player_id for player in result.optimal_lineup if player.player_id is not None
    }

    assert 202 in actual_ids
    assert 202 in optimal_ids
    assert 203 in actual_ids
    assert 203 in optimal_ids

    assert len(result.substitutions) == 1

    substitution = result.substitutions[0]

    assert substitution.player_out_name == "Devaughn Vele"
    assert substitution.player_in_name == "KC Concepcion"
    assert substitution.point_change == 5.3


def test_substitution_players_match_starter_set_difference() -> None:
    result = calculate_team_week_optimal_lineup(create_complete_roster())

    actual_ids = {
        player.player_id for player in result.actual_lineup if player.player_id is not None
    }
    optimal_ids = {
        player.player_id for player in result.optimal_lineup if player.player_id is not None
    }

    substitution_out_ids = {
        substitution.player_out_id
        for substitution in result.substitutions
        if substitution.player_out_id is not None
    }
    substitution_in_ids = {
        substitution.player_in_id
        for substitution in result.substitutions
        if substitution.player_in_id is not None
    }

    assert substitution_out_ids == actual_ids - optimal_ids
    assert substitution_in_ids == optimal_ids - actual_ids
