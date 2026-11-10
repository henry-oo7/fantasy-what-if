"""Matchup-aware optimal-lineup breakdowns."""

from dataclasses import dataclass
from typing import Literal

from app.analytics.optimal_lineup import (
    LineupPlayer,
    LineupSubstitution,
    TeamOptimalLineupSummary,
    TeamWeekOptimalLineup,
)
from app.domain.league import LeagueSnapshot

ResultType = Literal[
    "win",
    "loss",
    "tie",
]


@dataclass(frozen=True, slots=True)
class OptimalLineupRecord:
    wins: int
    losses: int
    ties: int
    win_percentage: float


@dataclass(frozen=True, slots=True)
class OptimalLineupPlayerChange:
    player_id: int
    player_name: str
    lineup_slot: str
    points: float
    default_position: str | None
    pro_team: str | None


@dataclass(frozen=True, slots=True)
class OptimalLineupPlayerMove:
    player_id: int
    player_name: str
    from_slot: str
    to_slot: str
    points: float


@dataclass(frozen=True, slots=True)
class TeamWeekOptimalLineupBreakdown:
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
    opponent_team_id: int
    opponent_team_name: str
    opponent_points: float
    actual_result: ResultType
    optimal_result: ResultType
    result_flipped: bool
    players_out: tuple[
        OptimalLineupPlayerChange,
        ...,
    ]
    players_in: tuple[
        OptimalLineupPlayerChange,
        ...,
    ]
    retained_player_moves: tuple[
        OptimalLineupPlayerMove,
        ...,
    ]
    net_point_gain: float


@dataclass(frozen=True, slots=True)
class TeamOptimalLineupBreakdown:
    team_id: int
    team_name: str
    weeks_played: int
    actual_points: float
    optimal_points: float
    missed_points: float
    efficiency_percentage: float
    actual_record: OptimalLineupRecord
    optimal_record: OptimalLineupRecord
    equivalent_wins_gained: float
    results_flipped: int
    weekly_results: tuple[
        TeamWeekOptimalLineupBreakdown,
        ...,
    ]


@dataclass(slots=True)
class _MutableRecord:
    wins: int = 0
    losses: int = 0
    ties: int = 0

    def add(self, result: ResultType) -> None:
        if result == "win":
            self.wins += 1
        elif result == "loss":
            self.losses += 1
        else:
            self.ties += 1

    def equivalent_wins(self) -> float:
        return self.wins + self.ties * 0.5

    def freeze(self) -> OptimalLineupRecord:
        decisions = self.wins + self.losses + self.ties

        win_percentage = 0.0 if decisions == 0 else self.equivalent_wins() / decisions

        return OptimalLineupRecord(
            wins=self.wins,
            losses=self.losses,
            ties=self.ties,
            win_percentage=round(
                win_percentage,
                4,
            ),
        )


def calculate_result(
    team_score: float,
    opponent_score: float,
) -> ResultType:
    if team_score > opponent_score:
        return "win"

    if team_score < opponent_score:
        return "loss"

    return "tie"


def build_optimal_lineup_breakdown(
    *,
    summary: TeamOptimalLineupSummary,
    snapshot: LeagueSnapshot,
) -> TeamOptimalLineupBreakdown:
    """Add canonical matchup context to stored lineup results."""

    team_names = {team.team_id: team.name for team in snapshot.teams}

    if summary.team_id not in team_names:
        raise ValueError(f"Team {summary.team_id} was not found in the league snapshot.")

    matchups_by_team_week: dict[
        tuple[int, int],
        tuple[int, float],
    ] = {}

    for matchup in snapshot.matchups:
        matchups_by_team_week[
            (
                matchup.home_team_id,
                matchup.week,
            )
        ] = (
            matchup.away_team_id,
            matchup.away_score,
        )
        matchups_by_team_week[
            (
                matchup.away_team_id,
                matchup.week,
            )
        ] = (
            matchup.home_team_id,
            matchup.home_score,
        )

    actual_record = _MutableRecord()
    optimal_record = _MutableRecord()
    weekly_breakdowns: list[TeamWeekOptimalLineupBreakdown] = []

    for weekly_result in summary.weekly_results:
        opponent = matchups_by_team_week.get(
            (
                summary.team_id,
                weekly_result.week,
            )
        )

        if opponent is None:
            raise ValueError(
                f"Week {weekly_result.week} does not "
                f"contain a completed matchup for "
                f"team {summary.team_id}."
            )

        opponent_team_id, opponent_points = opponent
        opponent_team_name = team_names.get(opponent_team_id)

        if opponent_team_name is None:
            raise ValueError(
                f"Week {weekly_result.week} references unknown opponent {opponent_team_id}."
            )

        actual_result = calculate_result(
            weekly_result.actual_points,
            opponent_points,
        )
        optimal_result = calculate_result(
            weekly_result.optimal_points,
            opponent_points,
        )

        actual_record.add(actual_result)
        optimal_record.add(optimal_result)

        weekly_breakdowns.append(
            _build_week_breakdown(
                weekly_result=weekly_result,
                opponent_team_id=opponent_team_id,
                opponent_team_name=(opponent_team_name),
                opponent_points=opponent_points,
                actual_result=actual_result,
                optimal_result=optimal_result,
            )
        )

    actual_frozen = actual_record.freeze()
    optimal_frozen = optimal_record.freeze()

    return TeamOptimalLineupBreakdown(
        team_id=summary.team_id,
        team_name=summary.team_name,
        weeks_played=summary.weeks_played,
        actual_points=summary.actual_points,
        optimal_points=summary.optimal_points,
        missed_points=summary.missed_points,
        efficiency_percentage=(summary.efficiency_percentage),
        actual_record=actual_frozen,
        optimal_record=optimal_frozen,
        equivalent_wins_gained=round(
            optimal_record.equivalent_wins() - actual_record.equivalent_wins(),
            2,
        ),
        results_flipped=sum(week.result_flipped for week in weekly_breakdowns),
        weekly_results=tuple(weekly_breakdowns),
    )


def _build_week_breakdown(
    *,
    weekly_result: TeamWeekOptimalLineup,
    opponent_team_id: int,
    opponent_team_name: str,
    opponent_points: float,
    actual_result: ResultType,
    optimal_result: ResultType,
) -> TeamWeekOptimalLineupBreakdown:
    actual_by_id = {
        player.player_id: player
        for player in weekly_result.actual_lineup
        if player.player_id is not None
    }
    optimal_by_id = {
        player.player_id: player
        for player in weekly_result.optimal_lineup
        if player.player_id is not None
    }

    actual_ids = set(actual_by_id)
    optimal_ids = set(optimal_by_id)

    players_out = tuple(
        _player_change(actual_by_id[player_id])
        for player_id in sorted(
            actual_ids - optimal_ids,
            key=lambda current_id: (
                actual_by_id[current_id].lineup_slot,
                -actual_by_id[current_id].points,
                current_id,
            ),
        )
    )

    players_in = tuple(
        _player_change(optimal_by_id[player_id])
        for player_id in sorted(
            optimal_ids - actual_ids,
            key=lambda current_id: (
                optimal_by_id[current_id].lineup_slot,
                -optimal_by_id[current_id].points,
                current_id,
            ),
        )
    )

    retained_player_moves = tuple(
        OptimalLineupPlayerMove(
            player_id=player_id,
            player_name=(optimal_by_id[player_id].player_name or "Unknown player"),
            from_slot=(actual_by_id[player_id].lineup_slot),
            to_slot=(optimal_by_id[player_id].lineup_slot),
            points=round(
                optimal_by_id[player_id].points,
                2,
            ),
        )
        for player_id in sorted(
            actual_ids & optimal_ids,
            key=lambda current_id: (
                actual_by_id[current_id].lineup_slot,
                optimal_by_id[current_id].lineup_slot,
                current_id,
            ),
        )
        if (actual_by_id[player_id].lineup_slot != optimal_by_id[player_id].lineup_slot)
    )

    players_out_points = round(
        sum(player.points for player in players_out),
        2,
    )
    players_in_points = round(
        sum(player.points for player in players_in),
        2,
    )
    net_point_gain = round(
        players_in_points - players_out_points,
        2,
    )

    if net_point_gain != weekly_result.missed_points:
        raise ValueError(
            "Optimal-lineup membership changes do not "
            "match the stored missed-point total for "
            f"team {weekly_result.team_id}, "
            f"week {weekly_result.week}."
        )

    return TeamWeekOptimalLineupBreakdown(
        team_id=weekly_result.team_id,
        team_name=weekly_result.team_name,
        week=weekly_result.week,
        actual_points=weekly_result.actual_points,
        optimal_points=weekly_result.optimal_points,
        missed_points=weekly_result.missed_points,
        efficiency_percentage=(weekly_result.efficiency_percentage),
        actual_starter_count=(weekly_result.actual_starter_count),
        optimal_filled_slot_count=(weekly_result.optimal_filled_slot_count),
        unfilled_slots=weekly_result.unfilled_slots,
        actual_lineup=weekly_result.actual_lineup,
        optimal_lineup=weekly_result.optimal_lineup,
        substitutions=weekly_result.substitutions,
        opponent_team_id=opponent_team_id,
        opponent_team_name=opponent_team_name,
        opponent_points=round(
            opponent_points,
            2,
        ),
        actual_result=actual_result,
        optimal_result=optimal_result,
        result_flipped=(actual_result != optimal_result),
        players_out=players_out,
        players_in=players_in,
        retained_player_moves=(retained_player_moves),
        net_point_gain=net_point_gain,
    )


def _player_change(
    player: LineupPlayer,
) -> OptimalLineupPlayerChange:
    if player.player_id is None or player.player_name is None:
        raise ValueError("A lineup membership change must reference a player.")

    return OptimalLineupPlayerChange(
        player_id=player.player_id,
        player_name=player.player_name,
        lineup_slot=player.lineup_slot,
        points=round(
            player.points,
            2,
        ),
        default_position=player.default_position,
        pro_team=player.pro_team,
    )
