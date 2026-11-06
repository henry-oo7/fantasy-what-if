from dataclasses import dataclass
from typing import Literal

from app.domain.league import LeagueSnapshot

ResultType = Literal["win", "loss", "tie"]


@dataclass(frozen=True, slots=True)
class Record:
    wins: int
    losses: int
    ties: int
    win_percentage: float


@dataclass(frozen=True, slots=True)
class LeagueAverageWeek:
    week: int
    team_score: float
    league_average: float
    actual_opponent_id: int
    actual_opponent_name: str
    actual_opponent_score: float
    actual_result: ResultType
    average_result: ResultType


@dataclass(frozen=True, slots=True)
class LeagueAverageStanding:
    rank: int
    team_id: int
    team_name: str
    actual_record: Record
    average_record: Record
    combined_record: Record
    points_for: float
    average_points_per_week: float
    weeks_played: int
    weekly_results: tuple[LeagueAverageWeek, ...]


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

    def to_record(self) -> Record:
        decisions = self.wins + self.losses + self.ties
        win_percentage = 0.0 if decisions == 0 else (self.wins + self.ties * 0.5) / decisions

        return Record(
            wins=self.wins,
            losses=self.losses,
            ties=self.ties,
            win_percentage=round(win_percentage, 4),
        )


@dataclass(slots=True)
class _TeamResult:
    actual: _MutableRecord
    average: _MutableRecord
    points_for: float
    weekly_results: list[LeagueAverageWeek]


def calculate_result(
    team_score: float,
    comparison_score: float,
) -> ResultType:
    if team_score > comparison_score:
        return "win"

    if team_score < comparison_score:
        return "loss"

    return "tie"


def calculate_league_average_standings(
    snapshot: LeagueSnapshot,
) -> tuple[LeagueAverageStanding, ...]:
    team_names = {team.team_id: team.name for team in snapshot.teams}

    team_results = {
        team.team_id: _TeamResult(
            actual=_MutableRecord(),
            average=_MutableRecord(),
            points_for=0.0,
            weekly_results=[],
        )
        for team in snapshot.teams
    }

    weekly_scores, weekly_opponents = _build_weekly_maps(snapshot)

    for week in sorted(weekly_scores):
        scores = weekly_scores[week]

        if not scores:
            continue

        league_average = sum(scores.values()) / len(scores)

        for team_id, team_score in scores.items():
            if team_id not in team_results:
                raise ValueError(f"Week {week} contains unknown team {team_id}.")

            opponent_id = weekly_opponents[week][team_id]

            if opponent_id not in team_names:
                raise ValueError(f"Week {week} contains unknown opponent {opponent_id}.")

            opponent_score = scores[opponent_id]

            actual_result = calculate_result(
                team_score,
                opponent_score,
            )
            average_result = calculate_result(
                team_score,
                league_average,
            )

            team_result = team_results[team_id]
            team_result.actual.add(actual_result)
            team_result.average.add(average_result)
            team_result.points_for += team_score
            team_result.weekly_results.append(
                LeagueAverageWeek(
                    week=week,
                    team_score=round(team_score, 2),
                    league_average=round(
                        league_average,
                        2,
                    ),
                    actual_opponent_id=opponent_id,
                    actual_opponent_name=team_names[opponent_id],
                    actual_opponent_score=round(
                        opponent_score,
                        2,
                    ),
                    actual_result=actual_result,
                    average_result=average_result,
                )
            )

    standings_without_rank = [
        _create_standing(
            team_id=team_id,
            team_name=team_names[team_id],
            result=team_result,
        )
        for team_id, team_result in team_results.items()
    ]

    ordered = sorted(
        standings_without_rank,
        key=lambda standing: (
            -standing.combined_record.win_percentage,
            -standing.average_record.win_percentage,
            -standing.points_for,
            standing.team_name.casefold(),
            standing.team_id,
        ),
    )

    return tuple(
        LeagueAverageStanding(
            rank=rank,
            team_id=standing.team_id,
            team_name=standing.team_name,
            actual_record=standing.actual_record,
            average_record=standing.average_record,
            combined_record=standing.combined_record,
            points_for=standing.points_for,
            average_points_per_week=(standing.average_points_per_week),
            weeks_played=standing.weeks_played,
            weekly_results=standing.weekly_results,
        )
        for rank, standing in enumerate(ordered, start=1)
    )


def _create_standing(
    *,
    team_id: int,
    team_name: str,
    result: _TeamResult,
) -> LeagueAverageStanding:
    actual_record = result.actual.to_record()
    average_record = result.average.to_record()

    combined_wins = actual_record.wins + average_record.wins
    combined_losses = actual_record.losses + average_record.losses
    combined_ties = actual_record.ties + average_record.ties
    combined_decisions = combined_wins + combined_losses + combined_ties

    combined_win_percentage = (
        0.0
        if combined_decisions == 0
        else (combined_wins + combined_ties * 0.5) / combined_decisions
    )

    combined_record = Record(
        wins=combined_wins,
        losses=combined_losses,
        ties=combined_ties,
        win_percentage=round(
            combined_win_percentage,
            4,
        ),
    )

    weeks_played = len(result.weekly_results)

    average_points_per_week = 0.0 if weeks_played == 0 else result.points_for / weeks_played

    return LeagueAverageStanding(
        rank=0,
        team_id=team_id,
        team_name=team_name,
        actual_record=actual_record,
        average_record=average_record,
        combined_record=combined_record,
        points_for=round(result.points_for, 2),
        average_points_per_week=round(
            average_points_per_week,
            2,
        ),
        weeks_played=weeks_played,
        weekly_results=tuple(result.weekly_results),
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

        for team_id in (
            matchup.home_team_id,
            matchup.away_team_id,
        ):
            if team_id in scores:
                raise ValueError(f"Team {team_id} appears more than once in week {matchup.week}.")

        scores[matchup.home_team_id] = matchup.home_score
        scores[matchup.away_team_id] = matchup.away_score

        opponents[matchup.home_team_id] = matchup.away_team_id
        opponents[matchup.away_team_id] = matchup.home_team_id

    return weekly_scores, weekly_opponents
