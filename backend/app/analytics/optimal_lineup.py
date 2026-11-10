from dataclasses import dataclass

from app.domain.lineup import (
    PlayerWeekSnapshot,
    TeamWeekRoster,
)


@dataclass(frozen=True, slots=True)
class LineupPlayer:
    lineup_slot: str
    player_id: int | None
    player_name: str | None
    default_position: str | None
    pro_team: str | None
    points: float
    actual_lineup_slot: str | None
    was_actual_starter: bool
    is_unfilled: bool


@dataclass(frozen=True, slots=True)
class LineupSubstitution:
    lineup_slot: str
    player_in_id: int | None
    player_in_name: str | None
    player_out_id: int | None
    player_out_name: str | None
    point_change: float


@dataclass(frozen=True, slots=True)
class TeamWeekOptimalLineup:
    team_id: int
    team_name: str
    week: int
    actual_points: float
    optimal_points: float
    missed_points: float
    efficiency_percentage: float
    actual_starter_count: int
    optimal_filled_slot_count: int
    unfilled_slots: tuple[str, ...]
    actual_lineup: tuple[LineupPlayer, ...]
    optimal_lineup: tuple[LineupPlayer, ...]
    substitutions: tuple[LineupSubstitution, ...]


@dataclass(frozen=True, slots=True)
class TeamOptimalLineupSummary:
    team_id: int
    team_name: str
    weeks_played: int
    actual_points: float
    optimal_points: float
    missed_points: float
    efficiency_percentage: float
    weekly_results: tuple[TeamWeekOptimalLineup, ...]


@dataclass(frozen=True, slots=True)
class _Assignment:
    slot: str
    player: PlayerWeekSnapshot | None


def calculate_team_week_optimal_lineup(
    roster: TeamWeekRoster,
) -> TeamWeekOptimalLineup:
    candidates = tuple(player for player in roster.players if not player.is_injured_reserve)

    optimal_assignments = _find_optimal_assignments(
        candidates,
        starting_slots=roster.starting_slots,
    )
    actual_assignments = _build_actual_assignments(
        roster.players,
        starting_slots=roster.starting_slots,
    )

    actual_points = round(
        sum(
            assignment.player.points
            for assignment in actual_assignments
            if assignment.player is not None
        ),
        2,
    )

    optimal_points = round(
        sum(
            assignment.player.points
            for assignment in optimal_assignments
            if assignment.player is not None
        ),
        2,
    )

    missed_points = round(
        max(optimal_points - actual_points, 0.0),
        2,
    )

    efficiency_percentage = round(
        100.0
        if optimal_points <= 0
        else min(
            actual_points / optimal_points * 100,
            100.0,
        ),
        2,
    )

    actual_lineup = tuple(_to_lineup_player(assignment) for assignment in actual_assignments)

    optimal_lineup = tuple(_to_lineup_player(assignment) for assignment in optimal_assignments)

    substitutions = _build_substitutions(
        actual_assignments=actual_assignments,
        optimal_assignments=optimal_assignments,
    )

    unfilled_slots = tuple(
        assignment.slot for assignment in optimal_assignments if assignment.player is None
    )

    return TeamWeekOptimalLineup(
        team_id=roster.team_id,
        team_name=roster.team_name,
        week=roster.week,
        actual_points=actual_points,
        optimal_points=optimal_points,
        missed_points=missed_points,
        efficiency_percentage=efficiency_percentage,
        actual_starter_count=sum(player.is_starter for player in roster.players),
        optimal_filled_slot_count=sum(
            assignment.player is not None for assignment in optimal_assignments
        ),
        unfilled_slots=unfilled_slots,
        actual_lineup=actual_lineup,
        optimal_lineup=optimal_lineup,
        substitutions=substitutions,
    )


def calculate_optimal_lineup_summaries(
    rosters: tuple[TeamWeekRoster, ...],
) -> tuple[TeamOptimalLineupSummary, ...]:
    weekly_results = tuple(calculate_team_week_optimal_lineup(roster) for roster in rosters)

    team_names = {result.team_id: result.team_name for result in weekly_results}

    results_by_team: dict[
        int,
        list[TeamWeekOptimalLineup],
    ] = {}

    for result in weekly_results:
        results_by_team.setdefault(
            result.team_id,
            [],
        ).append(result)

    summaries: list[TeamOptimalLineupSummary] = []

    for team_id, team_results in results_by_team.items():
        ordered_results = tuple(
            sorted(
                team_results,
                key=lambda result: result.week,
            )
        )

        actual_points = round(
            sum(result.actual_points for result in ordered_results),
            2,
        )
        optimal_points = round(
            sum(result.optimal_points for result in ordered_results),
            2,
        )
        missed_points = round(
            max(optimal_points - actual_points, 0.0),
            2,
        )
        efficiency_percentage = round(
            100.0
            if optimal_points <= 0
            else min(
                actual_points / optimal_points * 100,
                100.0,
            ),
            2,
        )

        summaries.append(
            TeamOptimalLineupSummary(
                team_id=team_id,
                team_name=team_names[team_id],
                weeks_played=len(ordered_results),
                actual_points=actual_points,
                optimal_points=optimal_points,
                missed_points=missed_points,
                efficiency_percentage=(efficiency_percentage),
                weekly_results=ordered_results,
            )
        )

    return tuple(
        sorted(
            summaries,
            key=lambda summary: (
                -summary.efficiency_percentage,
                summary.missed_points,
                summary.team_name.casefold(),
                summary.team_id,
            ),
        )
    )


def _find_optimal_assignments(
    players: tuple[PlayerWeekSnapshot, ...],
    *,
    starting_slots: tuple[str, ...],
) -> tuple[_Assignment, ...]:
    slot_order = tuple(
        sorted(
            enumerate(starting_slots),
            key=lambda item: (
                _eligible_player_count(
                    players,
                    item[1],
                ),
                item[0],
            ),
        )
    )

    best_score = float("-inf")
    best_assignments: tuple[_Assignment, ...] = ()
    current_assignments: dict[int, _Assignment] = {}

    def search(
        slot_index: int,
        used_player_ids: frozenset[int],
        score: float,
    ) -> None:
        nonlocal best_assignments
        nonlocal best_score

        if slot_index == len(slot_order):
            ordered_assignments = tuple(
                current_assignments[index] for index in range(len(starting_slots))
            )

            if score > best_score or (
                score == best_score
                and _assignment_key(ordered_assignments) < _assignment_key(best_assignments)
            ):
                best_score = score
                best_assignments = ordered_assignments

            return

        original_index, slot = slot_order[slot_index]

        eligible_players = sorted(
            (
                player
                for player in players
                if player.player_id not in used_player_ids and slot in player.eligible_lineup_slots
            ),
            key=lambda player: (
                -player.points,
                player.player_name.casefold(),
                player.player_id,
            ),
        )

        for player in eligible_players:
            current_assignments[original_index] = _Assignment(
                slot=slot,
                player=player,
            )

            search(
                slot_index + 1,
                used_player_ids | frozenset({player.player_id}),
                score + player.points,
            )

        current_assignments[original_index] = _Assignment(
            slot=slot,
            player=None,
        )

        search(
            slot_index + 1,
            used_player_ids,
            score,
        )

        del current_assignments[original_index]

    search(
        slot_index=0,
        used_player_ids=frozenset(),
        score=0.0,
    )

    return best_assignments


def _build_actual_assignments(
    players: tuple[PlayerWeekSnapshot, ...],
    *,
    starting_slots: tuple[str, ...],
) -> tuple[_Assignment, ...]:
    unused_starters = [player for player in players if player.is_starter]

    assignments: list[_Assignment] = []

    for slot in starting_slots:
        matching_index = next(
            (
                index
                for index, player in enumerate(unused_starters)
                if player.actual_lineup_slot == slot
            ),
            None,
        )

        if matching_index is None:
            assignments.append(
                _Assignment(
                    slot=slot,
                    player=None,
                )
            )
            continue

        player = unused_starters.pop(matching_index)

        assignments.append(
            _Assignment(
                slot=slot,
                player=player,
            )
        )

    return tuple(assignments)


def _build_substitutions(
    *,
    actual_assignments: tuple[_Assignment, ...],
    optimal_assignments: tuple[_Assignment, ...],
) -> tuple[LineupSubstitution, ...]:
    """Describe true starter-set changes.

    Players who remain in the starting lineup but move between
    eligible slots are not substitutions. This prevents positional
    exchanges, especially RB/WR/TE and FLEX rearrangements, from
    appearing as offsetting positive and negative changes.
    """

    actual_players = {
        assignment.player.player_id: assignment
        for assignment in actual_assignments
        if assignment.player is not None
    }
    optimal_players = {
        assignment.player.player_id: assignment
        for assignment in optimal_assignments
        if assignment.player is not None
    }

    exited_assignments = [
        assignment
        for player_id, assignment in actual_players.items()
        if player_id not in optimal_players
    ]
    entered_assignments = [
        assignment
        for player_id, assignment in optimal_players.items()
        if player_id not in actual_players
    ]

    exited_assignments.sort(
        key=lambda assignment: (
            assignment.player.points if assignment.player is not None else 0.0,
            assignment.slot,
            (assignment.player.player_id if assignment.player is not None else 10**12),
        )
    )
    entered_assignments.sort(
        key=lambda assignment: (
            -(assignment.player.points if assignment.player is not None else 0.0),
            assignment.slot,
            (assignment.player.player_id if assignment.player is not None else 10**12),
        )
    )

    substitutions: list[LineupSubstitution] = []
    pair_count = max(
        len(exited_assignments),
        len(entered_assignments),
    )

    for index in range(pair_count):
        actual = exited_assignments[index] if index < len(exited_assignments) else None
        optimal = entered_assignments[index] if index < len(entered_assignments) else None

        actual_player = actual.player if actual is not None else None
        optimal_player = optimal.player if optimal is not None else None

        actual_points = actual_player.points if actual_player is not None else 0.0
        optimal_points = optimal_player.points if optimal_player is not None else 0.0

        substitutions.append(
            LineupSubstitution(
                lineup_slot=(
                    optimal.slot
                    if optimal is not None
                    else actual.slot
                    if actual is not None
                    else "UNKNOWN"
                ),
                player_in_id=(optimal_player.player_id if optimal_player is not None else None),
                player_in_name=(optimal_player.player_name if optimal_player is not None else None),
                player_out_id=(actual_player.player_id if actual_player is not None else None),
                player_out_name=(actual_player.player_name if actual_player is not None else None),
                point_change=round(
                    optimal_points - actual_points,
                    2,
                ),
            )
        )

    return tuple(substitutions)


def _to_lineup_player(
    assignment: _Assignment,
) -> LineupPlayer:
    player = assignment.player

    if player is None:
        return LineupPlayer(
            lineup_slot=assignment.slot,
            player_id=None,
            player_name=None,
            default_position=None,
            pro_team=None,
            points=0.0,
            actual_lineup_slot=None,
            was_actual_starter=False,
            is_unfilled=True,
        )

    return LineupPlayer(
        lineup_slot=assignment.slot,
        player_id=player.player_id,
        player_name=player.player_name,
        default_position=player.default_position,
        pro_team=player.pro_team,
        points=round(player.points, 2),
        actual_lineup_slot=player.actual_lineup_slot,
        was_actual_starter=player.is_starter,
        is_unfilled=False,
    )


def _eligible_player_count(
    players: tuple[PlayerWeekSnapshot, ...],
    slot: str,
) -> int:
    return sum(slot in player.eligible_lineup_slots for player in players)


def _assignment_key(
    assignments: tuple[_Assignment, ...],
) -> tuple[int, ...]:
    if not assignments:
        return ()

    return tuple(
        (assignment.player.player_id if assignment.player is not None else 10**12)
        for assignment in assignments
    )
