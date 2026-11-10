from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.admin_dependencies import (
    SeasonImporter,
    get_season_importer,
)
from app.api.auth_dependencies import (
    AuthenticationContext,
    get_current_user,
    require_admin_user,
    require_session_csrf,
)
from app.api.dependencies import get_current_league_snapshot
from app.api.security import validate_request_origin
from app.db.session import SessionFactory
from app.domain.league import LeagueSnapshot
from app.providers.credential_vault import (
    ProviderCredentialConfigurationError,
)
from app.repositories.league_repository import (
    list_imported_seasons,
)
from app.schemas.leagues import (
    ImportedSeasonResponse,
    ImportedSeasonsResponse,
    ImportSeasonRequest,
    ImportSeasonResponse,
)
from app.services.active_season_context import (
    ViewSeasonResolutionError,
    resolve_view_season_context,
)
from app.services.season_import import (
    SeasonImportConflictError,
    SeasonImportUnavailableError,
    SeasonImportValidationError,
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
            context = resolve_view_season_context(
                session=session,
            )

            seasons = list_imported_seasons(
                session,
                installation_id=(context.installation_id),
                active_season_id=(context.season_id if context.is_synchronization_active else None),
            )
    except ViewSeasonResolutionError as exc:
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


@router.post(
    "/seasons/import",
    response_model=ImportSeasonResponse,
)
def import_another_season(
    payload: ImportSeasonRequest,
    _: Annotated[
        object,
        Depends(require_admin_user),
    ],
    __: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    ___: Annotated[
        AuthenticationContext,
        Depends(require_session_csrf),
    ],
    importer: Annotated[
        SeasonImporter,
        Depends(get_season_importer),
    ],
) -> ImportSeasonResponse:
    """Import another season for the installed league."""

    try:
        result = importer(
            season_year=payload.year,
        )
    except SeasonImportConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SeasonImportUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SeasonImportValidationError as exc:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
        ) from exc
    except ProviderCredentialConfigurationError as exc:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Provider credential encryption is not configured."),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=("Unable to import the requested fantasy season."),
        ) from exc

    sync_result = result.sync_result

    return ImportSeasonResponse(
        season_id=result.season_id,
        league_id=result.league_id,
        year=result.year,
        status=result.status.value,
        synchronization_enabled=(result.synchronization_enabled),
        teams_upserted=sync_result.teams_upserted,
        matchups_upserted=(sync_result.matchups_upserted),
        fantasy_byes_upserted=(sync_result.fantasy_byes_upserted),
        player_week_scores_upserted=(sync_result.player_week_scores_upserted),
    )
