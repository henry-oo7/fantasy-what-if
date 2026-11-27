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
)
from app.analytics.optimal_lineup_breakdown import (
    TeamOptimalLineupBreakdown,
    build_optimal_lineup_breakdown,
)
from app.analytics.schedule_swap import (
    ScheduleSwapResult,
    calculate_schedule_swap,
)
from app.api.auth_dependencies import (
    get_current_user,
    require_active_workspace,
)
from app.api.dependencies import (
    get_current_league_snapshot,
    get_persisted_optimal_lineup_summaries,
)
from app.domain.league import LeagueSnapshot

router = APIRouter(
    prefix="/api/analytics",
    tags=["analytics"],
    dependencies=[
        Depends(get_current_user),
        Depends(require_active_workspace),
    ],
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
    return schedule_swap_or_422(snapshot, first_team_id, second_team_id)


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
    summaries: Annotated[
        tuple[TeamOptimalLineupSummary, ...],
        Depends(get_persisted_optimal_lineup_summaries),
    ],
) -> tuple[TeamOptimalLineupSummary, ...]:
    return summaries


@router.get("/optimal-lineups/{team_id}")
def read_team_optimal_lineup_summary(
    team_id: int,
    summaries: Annotated[
        tuple[TeamOptimalLineupSummary, ...],
        Depends(get_persisted_optimal_lineup_summaries),
    ],
    snapshot: Annotated[
        LeagueSnapshot,
        Depends(get_current_league_snapshot),
    ],
) -> TeamOptimalLineupBreakdown:
    return team_optimal_lineup_breakdown(team_id, summaries, snapshot)


# Shared with the personal Season routes; callers authorize and load first.


def schedule_swap_or_422(
    snapshot: LeagueSnapshot, first_team_id: int, second_team_id: int
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


def team_optimal_lineup_breakdown(
    team_id: int,
    summaries: tuple[TeamOptimalLineupSummary, ...],
    snapshot: LeagueSnapshot,
) -> TeamOptimalLineupBreakdown:
    summary = next(
        (current_summary for current_summary in summaries if current_summary.team_id == team_id),
        None,
    )

    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team {team_id} was not found.",
        )

    try:
        return build_optimal_lineup_breakdown(
            summary=summary,
            snapshot=snapshot,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
        ) from exc
