from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

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
from app.auth.security import (
    InvalidPasswordError,
    InvalidUsernameError,
)
from app.db.auth_models import User
from app.db.session import (
    SessionFactory,
    get_database_session,
)
from app.repositories.auth_repository import (
    DuplicateEmailError,
    DuplicateUsernameError,
)
from app.repositories.sync_repository import (
    StoredSyncStatus,
    get_sync_status,
)
from app.schemas.admin import (
    CreateSpectatorRequest,
    ResetSpectatorPasswordRequest,
    SetSpectatorAccessRequest,
    SpectatorAccountMutationResponse,
    SpectatorAccountResponse,
    SyncRequest,
    SyncResultResponse,
    SyncRunResponse,
    SyncStatusResponse,
    UpdateSpectatorProfileRequest,
)
from app.services.provider_adapter_resolver import (
    resolve_active_provider_context,
)
from app.services.spectator_account import (
    SpectatorAccountStateError,
    SpectatorAlreadyExistsError,
    SpectatorNotFoundError,
    create_spectator_account,
    get_spectator_account,
    reset_spectator_password,
    set_spectator_active,
    update_spectator_profile,
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


@router.get("/spectator")
def read_spectator_account(
    session: Annotated[
        Session,
        Depends(get_database_session),
    ],
) -> SpectatorAccountResponse:
    """Return safe shared spectator account metadata."""

    try:
        spectator = get_spectator_account(session)
    except SpectatorAccountStateError as exc:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=("Spectator account configuration is invalid."),
        ) from exc

    return _build_spectator_response(spectator)


@router.post("/spectator")
def create_admin_spectator_account(
    payload: CreateSpectatorRequest,
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    __: Annotated[
        AuthenticationContext,
        Depends(require_session_csrf),
    ],
    session: Annotated[
        Session,
        Depends(get_database_session),
    ],
) -> SpectatorAccountMutationResponse:
    """Create the installation's shared spectator."""

    try:
        spectator = create_spectator_account(
            session,
            username=payload.username,
            display_name=payload.display_name,
            email=payload.email,
            password=payload.password,
        )

        session.commit()
    except SpectatorAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except (
        DuplicateUsernameError,
        DuplicateEmailError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except (
        InvalidUsernameError,
        InvalidPasswordError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
        ) from exc

    return _build_spectator_mutation_response(
        spectator,
    )


@router.patch("/spectator/profile")
def update_admin_spectator_profile(
    payload: UpdateSpectatorProfileRequest,
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    __: Annotated[
        AuthenticationContext,
        Depends(require_session_csrf),
    ],
    session: Annotated[
        Session,
        Depends(get_database_session),
    ],
) -> SpectatorAccountMutationResponse:
    """Update shared spectator profile metadata."""

    try:
        spectator = update_spectator_profile(
            session,
            display_name=payload.display_name,
            email=payload.email,
        )

        session.commit()
    except SpectatorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except SpectatorAccountStateError as exc:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=("Spectator account configuration is invalid."),
        ) from exc
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
        ) from exc

    return _build_spectator_mutation_response(
        spectator,
    )


@router.post("/spectator/reset-password")
def reset_admin_spectator_password(
    payload: ResetSpectatorPasswordRequest,
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    __: Annotated[
        AuthenticationContext,
        Depends(require_session_csrf),
    ],
    session: Annotated[
        Session,
        Depends(get_database_session),
    ],
) -> SpectatorAccountMutationResponse:
    """Reset the password and revoke spectator sessions."""

    try:
        spectator, revoked_sessions = reset_spectator_password(
            session,
            password=payload.password,
        )

        session.commit()
    except SpectatorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except SpectatorAccountStateError as exc:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=("Spectator account configuration is invalid."),
        ) from exc
    except InvalidPasswordError as exc:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(exc),
        ) from exc

    return _build_spectator_mutation_response(
        spectator,
        revoked_sessions=revoked_sessions,
    )


@router.patch("/spectator/access")
def set_admin_spectator_access(
    payload: SetSpectatorAccessRequest,
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    __: Annotated[
        AuthenticationContext,
        Depends(require_session_csrf),
    ],
    session: Annotated[
        Session,
        Depends(get_database_session),
    ],
) -> SpectatorAccountMutationResponse:
    """Enable or disable shared spectator access."""

    try:
        spectator, revoked_sessions = set_spectator_active(
            session,
            is_active=payload.is_active,
        )

        session.commit()
    except SpectatorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except SpectatorAccountStateError as exc:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=("Spectator account configuration is invalid."),
        ) from exc

    return _build_spectator_mutation_response(
        spectator,
        revoked_sessions=revoked_sessions,
    )


def _build_spectator_response(
    spectator: User | None,
) -> SpectatorAccountResponse:
    if spectator is None:
        return SpectatorAccountResponse(
            configured=False,
        )

    return SpectatorAccountResponse(
        configured=True,
        id=spectator.id,
        username=spectator.username,
        display_name=spectator.display_name,
        email=spectator.email,
        is_active=spectator.is_active,
    )


def _build_spectator_mutation_response(
    spectator: User,
    *,
    revoked_sessions: int = 0,
) -> SpectatorAccountMutationResponse:
    return SpectatorAccountMutationResponse(
        configured=True,
        id=spectator.id,
        username=spectator.username,
        display_name=spectator.display_name,
        email=spectator.email,
        is_active=spectator.is_active,
        revoked_sessions=revoked_sessions,
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
