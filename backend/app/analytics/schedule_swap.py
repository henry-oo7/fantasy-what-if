from dataclasses import dataclass
from typing import Literal

from app.domain.league import LeagueSnapshot

ResultType = Literal["win", "loss", "tie"]


@dataclass(frozen=True, slots=True)
class ScheduleRecord:
    wins: int
    losses: int
    ties: int
    win_percentage: float


@dataclass(frozen=True, slots=True)
class ScheduleSwapWeek:
    week: int
    team_id: int
    team_name: str
    borrowed_schedule_team_id: int
    borrowed_schedule_team_name: str
    hypothetical_opponent_id: int
    hypothetical_opponent_name: str
    team_score: float
    opponent_score: float
    result: ResultType


@dataclass(frozen=True, slots=True)
class TeamScheduleSwapResult:
    team_id: int
    team_name: str
    borrowed_schedule_team_id: int
    borrowed_schedule_team_name: str
    original_record: ScheduleRecord
    swapped_record: ScheduleRecord
    equivalent_wins_change: float
    weekly_results: tuple[ScheduleSwapWeek, ...]


@dataclass(frozen=True, slots=True)
class ScheduleSwapResult:
    first_team: TeamScheduleSwapResult
    second_team: TeamScheduleSwapResult
    completed_weeks: tuple[int, ...]


@dataclass(slots=True)
class _MutableRecord:
    wins: int = 0
    losses: int = 0
    ties: int = 0

    def add_result(self, result: ResultType) -> None:
        if result == "win":
            self.wins += 1
        elif result == "loss":
            self.losses += 1
        else:
            self.ties += 1

    def to_record(self) -> ScheduleRecord:
        decisions = self.wins + self.losses + self.ties

        win_percentage = 0.0 if decisions == 0 else (self.wins + self.ties * 0.5) / decisions

        return ScheduleRecord(
            wins=self.wins,
            losses=self.losses,
            ties=self.ties,
            win_percentage=round(win_percentage, 4),
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


def equivalent_wins(record: ScheduleRecord) -> float:
    return record.wins + record.ties * 0.5


def calculate_schedule_swap(
    snapshot: LeagueSnapshot,
    *,
    first_team_id: int,
    second_team_id: int,
) -> ScheduleSwapResult:
    if first_team_id == second_team_id:
        raise ValueError("Schedule swaps require two different teams.")

    team_names = {team.team_id: team.name for team in snapshot.teams}

    missing_team_ids = sorted(
        team_id for team_id in (first_team_id, second_team_id) if team_id not in team_names
    )

    if missing_team_ids:
        raise ValueError(
            "Unknown team ID values: " + ", ".join(str(team_id) for team_id in missing_team_ids)
        )

    weekly_scores, weekly_opponents = _build_weekly_maps(snapshot)

    completed_weeks = tuple(
        week
        for week in sorted(weekly_scores)
        if first_team_id in weekly_scores[week] and second_team_id in weekly_scores[week]
    )

    first_result = _calculate_team_schedule_swap(
        team_id=first_team_id,
        borrowed_schedule_team_id=second_team_id,
        team_names=team_names,
        weekly_scores=weekly_scores,
        weekly_opponents=weekly_opponents,
        completed_weeks=completed_weeks,
    )

    second_result = _calculate_team_schedule_swap(
        team_id=second_team_id,
        borrowed_schedule_team_id=first_team_id,
        team_names=team_names,
        weekly_scores=weekly_scores,
        weekly_opponents=weekly_opponents,
        completed_weeks=completed_weeks,
    )

    return ScheduleSwapResult(
        first_team=first_result,
        second_team=second_result,
        completed_weeks=completed_weeks,
    )


def _calculate_team_schedule_swap(
    *,
    team_id: int,
    borrowed_schedule_team_id: int,
    team_names: dict[int, str],
    weekly_scores: dict[int, dict[int, float]],
    weekly_opponents: dict[int, dict[int, int]],
    completed_weeks: tuple[int, ...],
) -> TeamScheduleSwapResult:
    original_record = _MutableRecord()
    swapped_record = _MutableRecord()
    weekly_results: list[ScheduleSwapWeek] = []

    for week in completed_weeks:
        scores = weekly_scores[week]
        opponents = weekly_opponents[week]

        team_score = scores[team_id]
        original_opponent_id = opponents[team_id]
        borrowed_opponent_id = opponents[borrowed_schedule_team_id]

        original_record.add_result(
            calculate_result(
                team_score,
                scores[original_opponent_id],
            )
        )

        if borrowed_opponent_id == team_id:
            hypothetical_opponent_id = original_opponent_id
        else:
            hypothetical_opponent_id = borrowed_opponent_id

        opponent_score = scores[hypothetical_opponent_id]
        result = calculate_result(team_score, opponent_score)

        swapped_record.add_result(result)

        weekly_results.append(
            ScheduleSwapWeek(
                week=week,
                team_id=team_id,
                team_name=team_names[team_id],
                borrowed_schedule_team_id=(borrowed_schedule_team_id),
                borrowed_schedule_team_name=team_names[borrowed_schedule_team_id],
                hypothetical_opponent_id=(hypothetical_opponent_id),
                hypothetical_opponent_name=team_names[hypothetical_opponent_id],
                team_score=round(team_score, 2),
                opponent_score=round(opponent_score, 2),
                result=result,
            )
        )

    original = original_record.to_record()
    swapped = swapped_record.to_record()

    return TeamScheduleSwapResult(
        team_id=team_id,
        team_name=team_names[team_id],
        borrowed_schedule_team_id=borrowed_schedule_team_id,
        borrowed_schedule_team_name=team_names[borrowed_schedule_team_id],
        original_record=original,
        swapped_record=swapped,
        equivalent_wins_change=round(
            equivalent_wins(swapped) - equivalent_wins(original),
            2,
        ),
        weekly_results=tuple(weekly_results),
    )


def _build_weekly_maps(
    snapshot: LeagueSnapshot,
) -> tuple[
    dict[int, dict[int, float]],
    dict[int, dict[int, int]],
]:
    weekly_scores: dict[int, dict[int, float]] = {}
    weekly_opponents: dict[int, dict[int, int]] = {}

    for matchup in snapshot.matchups:
        if matchup.week >= snapshot.current_week:
            continue

        scores = weekly_scores.setdefault(
            matchup.week,
            {},
        )
        opponents = weekly_opponents.setdefault(
            matchup.week,
            {},
        )

        team_ids = (
            matchup.home_team_id,
            matchup.away_team_id,
        )

        for team_id in team_ids:
            if team_id in scores:
                raise ValueError(f"Team {team_id} appears more than once in week {matchup.week}.")

        scores[matchup.home_team_id] = matchup.home_score
        scores[matchup.away_team_id] = matchup.away_score

        opponents[matchup.home_team_id] = matchup.away_team_id
        opponents[matchup.away_team_id] = matchup.home_team_id

    return weekly_scores, weekly_opponents
