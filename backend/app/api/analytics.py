from typing import Annotated

from fastapi import APIRouter, Depends

from app.analytics.all_play import (
    AllPlayStanding,
    calculate_all_play_standings,
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
