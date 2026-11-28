"""Personal League removal route (ADR 0010, FR-16)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import AuthenticationContext, require_session_csrf
from app.api.destructive_throttle import record_destructive_attempt
from app.api.security import validate_request_origin
from app.core.client_ip import resolve_client_ip
from app.core.settings import Settings, get_settings
from app.db.platform_enums import FantasyProvider
from app.db.session import SessionFactory
from app.schemas.league_removal import LeagueRemovalRequest, LeagueRemovedResponse
from app.services.league_removal import (
    LEAGUE_REMOVAL_ATTEMPT_WINDOW_MINUTES,
    LEAGUE_REMOVAL_MAXIMUM_ATTEMPTS,
    LEAGUE_REMOVAL_REQUEST_ACTION,
    LeagueRemovalConfirmationError,
    LeagueRemovalError,
    remove_league_access,
)

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/leagues", tags=["leagues"])

_UNAVAILABLE = {
    "detail": "League removal is unavailable.",
    "code": "league_removal_unavailable",
    "retryable": False,
}


@router.post("/{provider}/{provider_league_id}/removal", response_model=LeagueRemovedResponse)
def remove_league(
    payload: LeagueRemovalRequest,
    request: Request,
    provider: Annotated[str, Path(max_length=32)],
    provider_league_id: Annotated[str, Path(max_length=64)],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[AuthenticationContext, Depends(require_session_csrf)],
    _: Annotated[None, Depends(validate_request_origin)],
) -> JSONResponse:
    """Remove this account's access to one League. Nothing else is touched."""

    throttled = record_destructive_attempt(
        settings,
        action=LEAGUE_REMOVAL_REQUEST_ACTION,
        user_id=context.user_id,
        source=resolve_client_ip(request),
        maximum_requests=LEAGUE_REMOVAL_MAXIMUM_ATTEMPTS,
        window_minutes=LEAGUE_REMOVAL_ATTEMPT_WINDOW_MINUTES,
        code="league_removal_throttled",
    )

    if throttled is not None:
        return throttled

    try:
        league_provider = FantasyProvider(provider)
    except ValueError:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=_UNAVAILABLE)

    try:
        with SessionFactory() as session:
            remove_league_access(
                session,
                user_id=context.user_id,
                auth_session_id=context.session_id,
                provider=league_provider,
                provider_league_id=provider_league_id,
                confirmation=payload.confirmation,
            )
            session.commit()
    except LeagueRemovalConfirmationError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": "Type the League name exactly as shown.",
                "code": exc.code,
                "retryable": True,
            },
        )
    except LeagueRemovalError:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=_UNAVAILABLE)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=LeagueRemovedResponse(
            detail="The League was removed from your account."
        ).model_dump(mode="json"),
    )
