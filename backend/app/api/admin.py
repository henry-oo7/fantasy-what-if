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
from app.core.settings import Settings, get_settings
from app.db.auth_models import User
from app.db.session import SessionFactory
from app.repositories.invitation_repository import (
    InvitationRepository,
    InviteUnavailableError,
    TeamNotFoundError,
    list_invitations_with_teams,
)
from app.repositories.sync_repository import (
    StoredSyncStatus,
    get_sync_status,
)
from app.schemas.admin import (
    CreatedInvitationResponse,
    CreateInvitationRequest,
    InvitationListItemResponse,
    InvitationListResponse,
    RevokeInvitationResponse,
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


@router.get("/invitations")
def read_registration_invitations(
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
) -> InvitationListResponse:
    with SessionFactory() as session:
        items = list_invitations_with_teams(
            session,
            espn_league_id=settings.espn_league_id,
            season_year=settings.espn_season,
        )

        return InvitationListResponse(
            invitations=tuple(
                InvitationListItemResponse(
                    id=item.invitation.id,
                    team_id=item.team_id,
                    team_name=item.team_name,
                    season=item.season_year,
                    intended_display_name=(item.invitation.intended_display_name),
                    status=(item.invitation.status.value),
                    created_at=(item.invitation.created_at),
                    expires_at=(item.invitation.expires_at),
                    used_at=item.invitation.used_at,
                    revoked_at=(item.invitation.revoked_at),
                )
                for item in items
            )
        )


@router.post(
    "/invitations",
    status_code=status.HTTP_201_CREATED,
)
def create_registration_invitation(
    payload: CreateInvitationRequest,
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
    admin_user: Annotated[
        User,
        Depends(require_admin_user),
    ],
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    __: Annotated[
        AuthenticationContext,
        Depends(require_session_csrf),
    ],
) -> CreatedInvitationResponse:
    try:
        with SessionFactory() as session:
            stored_admin = session.get(
                User,
                admin_user.id,
            )

            if stored_admin is None:
                raise HTTPException(
                    status_code=(status.HTTP_401_UNAUTHORIZED),
                    detail=("Administrator account was not found."),
                )

            repository = InvitationRepository(session)

            created = repository.create_invitation(
                admin_user=stored_admin,
                espn_league_id=(settings.espn_league_id),
                season_year=settings.espn_season,
                espn_team_id=payload.team_id,
                lifetime_hours=(settings.invite_lifetime_hours),
                intended_display_name=(payload.intended_display_name),
            )

            invitation_id = created.invitation.id
            intended_display_name = created.invitation.intended_display_name
            expires_at = created.invitation.expires_at

            session.commit()

        registration_url = (
            settings.frontend_app_url.rstrip("/") + "/register?invite=" + created.raw_token
        )

        return CreatedInvitationResponse(
            id=invitation_id,
            team_id=payload.team_id,
            team_name=created.team_name,
            season=created.season_year,
            intended_display_name=(intended_display_name),
            expires_at=expires_at,
            registration_url=registration_url,
        )

    except TeamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/invitations/{invitation_id}/revoke",
)
def revoke_registration_invitation(
    invitation_id: int,
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
) -> RevokeInvitationResponse:
    try:
        with SessionFactory() as session:
            repository = InvitationRepository(session)

            invitation = repository.get_invitation_by_id(invitation_id)

            if invitation is None:
                raise HTTPException(
                    status_code=(status.HTTP_404_NOT_FOUND),
                    detail=("Registration invitation was not found."),
                )

            repository.revoke_invitation(invitation)
            session.commit()

            if invitation.revoked_at is None:
                raise RuntimeError("Invitation revocation timestamp was not recorded.")

            return RevokeInvitationResponse(
                id=invitation.id,
                status=invitation.status.value,
                revoked_at=invitation.revoked_at,
            )

    except InviteUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
