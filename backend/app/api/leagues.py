from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth_dependencies import get_current_user
from app.api.dependencies import get_current_league_snapshot
from app.db.session import SessionFactory
from app.domain.league import LeagueSnapshot
from app.repositories.league_repository import (
    list_imported_seasons,
)
from app.schemas.leagues import (
    ImportedSeasonResponse,
    ImportedSeasonsResponse,
)
from app.services.active_season_context import (
    ActiveSeasonResolutionError,
    resolve_active_season_context,
)

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


@router.get("/seasons")
def read_imported_seasons() -> ImportedSeasonsResponse:
    """Return seasons already imported for this installation."""

    try:
        with SessionFactory() as session:
            context = resolve_active_season_context(
                session=session,
            )

            seasons = list_imported_seasons(
                session,
                installation_id=(context.installation_id),
                active_season_id=(context.season_id),
            )
    except ActiveSeasonResolutionError as exc:
        from fastapi import (
            HTTPException,
            status,
        )

        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(exc),
        ) from exc

    return ImportedSeasonsResponse(
        seasons=tuple(
            ImportedSeasonResponse(
                season_id=season.season_id,
                league_id=season.league_id,
                league_name=season.league_name,
                year=season.year,
                status=season.status,
                is_active=season.is_active,
                last_synced_at=(season.last_synced_at),
            )
            for season in seasons
        )
    )
