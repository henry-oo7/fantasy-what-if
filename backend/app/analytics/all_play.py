from dataclasses import dataclass
from itertools import combinations

from app.domain.league import LeagueSnapshot


@dataclass(frozen=True, slots=True)
class AllPlayStanding:
    rank: int
    team_id: int
    team_name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    win_percentage: float


@dataclass(slots=True)
class _AllPlayRecord:
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0

    @property
    def decisions(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def win_percentage(self) -> float:
        if self.decisions == 0:
            return 0.0

        return (self.wins + (self.ties * 0.5)) / self.decisions


def calculate_all_play_standings(
    snapshot: LeagueSnapshot,
) -> tuple[AllPlayStanding, ...]:
    team_names = {team.team_id: team.name for team in snapshot.teams}

    records = {team.team_id: _AllPlayRecord() for team in snapshot.teams}

    weekly_scores = _collect_completed_weekly_scores(snapshot)

    for scores in weekly_scores.values():
        for team_id, score in scores.items():
            records[team_id].points_for += score

        for first_team_id, second_team_id in combinations(sorted(scores), 2):
            first_score = scores[first_team_id]
            second_score = scores[second_team_id]

            if first_score > second_score:
                records[first_team_id].wins += 1
                records[second_team_id].losses += 1
            elif second_score > first_score:
                records[second_team_id].wins += 1
                records[first_team_id].losses += 1
            else:
                records[first_team_id].ties += 1
                records[second_team_id].ties += 1

    ordered_team_ids = sorted(
        records,
        key=lambda team_id: (
            -records[team_id].win_percentage,
            -records[team_id].points_for,
            team_names[team_id].casefold(),
            team_id,
        ),
    )

    return tuple(
        AllPlayStanding(
            rank=rank,
            team_id=team_id,
            team_name=team_names[team_id],
            wins=records[team_id].wins,
            losses=records[team_id].losses,
            ties=records[team_id].ties,
            points_for=round(records[team_id].points_for, 2),
            win_percentage=round(records[team_id].win_percentage, 4),
        )
        for rank, team_id in enumerate(ordered_team_ids, start=1)
    )


def _collect_completed_weekly_scores(
    snapshot: LeagueSnapshot,
) -> dict[int, dict[int, float]]:
    weekly_scores: dict[int, dict[int, float]] = {}

    for matchup in snapshot.matchups:
        if matchup.week >= snapshot.current_week:
            continue

        week_scores = weekly_scores.setdefault(matchup.week, {})

        week_scores[matchup.home_team_id] = matchup.home_score
        week_scores[matchup.away_team_id] = matchup.away_score

    return weekly_scores
