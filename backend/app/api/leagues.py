from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth_dependencies import get_current_user
from app.api.dependencies import get_current_league_snapshot
from app.domain.league import LeagueSnapshot

router = APIRouter(
    prefix="/api/leagues",
    tags=["leagues"],
    dependencies=[
        Depends(get_current_user),
    ],
)


@router.get("/current")
def read_current_league(
    snapshot: Annotated[
        LeagueSnapshot,
        Depends(get_current_league_snapshot),
    ],
) -> LeagueSnapshot:
    return snapshot
