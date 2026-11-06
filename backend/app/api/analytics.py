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
from app.analytics.schedule_swap import (
    ScheduleSwapResult,
    calculate_schedule_swap,
)
from app.api.dependencies import get_current_league_snapshot
from app.domain.league import LeagueSnapshot

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
