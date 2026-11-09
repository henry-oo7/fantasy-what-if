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
    )
