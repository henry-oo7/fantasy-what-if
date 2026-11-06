from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.analytics.all_play import (
    AllPlayStanding,
    calculate_all_play_standings,
)
from app.analytics.league_average import (
    LeagueAverageStanding,
    calculate_league_average_standings,
)
from app.analytics.optimal_lineup import (
    TeamOptimalLineupSummary,
    calculate_optimal_lineup_summaries,
)
from app.analytics.schedule_swap import (
    ScheduleSwapResult,
    calculate_schedule_swap,
)
from app.api.dependencies import (
    get_completed_team_week_rosters,
    get_current_league_snapshot,
)
from app.domain.league import LeagueSnapshot
from app.domain.lineup import TeamWeekRoster

router = APIRouter(
    prefix="/api/analytics",
    tags=["analytics"],
)


@router.get("/all-play")
def read_all_play_standings(
    snapshot: Annotated[
        LeagueSnapshot,
        Depends(get_current_league_snapshot),
    ],
) -> tuple[AllPlayStanding, ...]:
    return calculate_all_play_standings(snapshot)


@router.get("/schedule-swap")
def read_schedule_swap(
    snapshot: Annotated[
        LeagueSnapshot,
        Depends(get_current_league_snapshot),
    ],
    first_team_id: Annotated[
        int,
        Query(gt=0),
    ],
    second_team_id: Annotated[
        int,
        Query(gt=0),
    ],
) -> ScheduleSwapResult:
    try:
        return calculate_schedule_swap(
            snapshot,
            first_team_id=first_team_id,
            second_team_id=second_team_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get("/league-average")
def read_league_average_standings(
    snapshot: Annotated[
        LeagueSnapshot,
        Depends(get_current_league_snapshot),
    ],
) -> tuple[LeagueAverageStanding, ...]:
    return calculate_league_average_standings(snapshot)


@router.get("/optimal-lineups")
def read_optimal_lineup_summaries(
    rosters: Annotated[
        tuple[TeamWeekRoster, ...],
        Depends(get_completed_team_week_rosters),
    ],
) -> tuple[TeamOptimalLineupSummary, ...]:
    return calculate_optimal_lineup_summaries(rosters)


@router.get("/optimal-lineups/{team_id}")
def read_team_optimal_lineup_summary(
    team_id: int,
    rosters: Annotated[
        tuple[TeamWeekRoster, ...],
        Depends(get_completed_team_week_rosters),
    ],
) -> TeamOptimalLineupSummary:
    summaries = calculate_optimal_lineup_summaries(rosters)

    summary = next(
        (current_summary for current_summary in summaries if current_summary.team_id == team_id),
        None,
    )

    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team {team_id} was not found.",
        )

    return summary
