from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.admin_dependencies import (
    SynchronizationRunner,
    get_synchronization_runner,
)
from app.api.auth_dependencies import (
    AuthenticationContext,
    require_admin_user,
    require_session_csrf,
)
from app.api.security import validate_request_origin
from app.db.auth_models import User
from app.db.session import SessionFactory
from app.repositories.sync_repository import (
    StoredSyncStatus,
    get_sync_status,
)
from app.schemas.admin import (
    SyncRequest,
    SyncResultResponse,
    SyncRunResponse,
    SyncStatusResponse,
)
from app.services.provider_adapter_resolver import (
    resolve_active_provider_context,
)
from app.services.sync_lock import (
    SyncAlreadyRunningError,
)

router = APIRouter(
    prefix="/api/admin",
    tags=["administration"],
    dependencies=[
        Depends(require_admin_user),
    ],
)


@router.get("/sync-status")
def read_sync_status() -> SyncStatusResponse:
    with SessionFactory() as session:
        _, season, _ = resolve_active_provider_context(session=session)

        stored = get_sync_status(
            session,
            season_id=season.id,
        )

    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=("Synchronized provider data was not found."),
        )

    return _build_sync_status_response(stored)


@router.post("/sync")
def run_admin_synchronization(
    payload: SyncRequest,
    _: Annotated[
        User,
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
    runner: Annotated[
        SynchronizationRunner,
        Depends(get_synchronization_runner),
    ],
) -> SyncResultResponse:
    try:
        result = runner(full_sync=payload.full_sync)
    except SyncAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=("Unable to synchronize fantasy provider data."),
        ) from exc

    return SyncResultResponse(
        provider=result.provider.value,
        season_id=result.season_id,
        provider_league_id=(result.provider_league_id),
        provider_season_id=(result.provider_season_id),
        current_week=result.current_week,
        full_sync=result.full_sync,
        player_weeks=result.player_weeks,
        teams_upserted=result.teams_upserted,
        matchups_upserted=result.matchups_upserted,
        fantasy_byes_upserted=(result.fantasy_byes_upserted),
        player_week_scores_upserted=(result.player_week_scores_upserted),
    )


def _build_sync_status_response(
    stored: StoredSyncStatus,
) -> SyncStatusResponse:
    latest_run = stored.latest_run

    return SyncStatusResponse(
        provider=stored.provider.value,
        season_id=stored.season_id,
        provider_league_id=(stored.provider_league_id),
        provider_season_id=(stored.provider_season_id),
        last_synced_at=stored.last_synced_at,
        latest_run=(
            SyncRunResponse(
                id=latest_run.id,
                status=latest_run.status.value,
                is_full_sync=latest_run.is_full_sync,
                started_at=latest_run.started_at,
                completed_at=latest_run.completed_at,
                requested_from_week=(latest_run.requested_from_week),
                requested_through_week=(latest_run.requested_through_week),
                teams_upserted=(latest_run.teams_upserted),
                matchups_upserted=(latest_run.matchups_upserted),
                player_week_scores_upserted=(latest_run.player_week_scores_upserted),
                safe_error_message=(latest_run.safe_error_message),
            )
            if latest_run is not None
            else None
        ),
    )
