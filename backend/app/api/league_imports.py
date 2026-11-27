"""Personal League import endpoint (ADR 0010).

Additive and transitional: current workspace onboarding and workspace APIs
are unchanged, and the imported access belongs to the User.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import AuthenticationContext, require_session_csrf
from app.api.onboarding import get_espn_http_get
from app.api.security import validate_request_origin
from app.core.client_ip import resolve_client_ip
from app.core.settings import Settings, get_settings
from app.db.session import SessionFactory
from app.providers.espn.league_access import HttpGet
from app.schemas.league_imports import ESPN_PROVIDER, LeagueImportRequest, LeagueImportResponse
from app.services.league_import import import_league
from app.services.provider_onboarding import (
    ProviderOnboardingError,
    private_access_from_values,
    unsupported_provider,
)

router = APIRouter(
    prefix="/api/league-imports",
    tags=["league-imports"],
)


@router.post("", response_model=LeagueImportResponse)
def create_league_import(
    payload: LeagueImportRequest,
    request: Request,
    context: Annotated[AuthenticationContext, Depends(require_session_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
    http_get: Annotated[HttpGet | None, Depends(get_espn_http_get)],
    _: Annotated[None, Depends(validate_request_origin)],
) -> LeagueImportResponse | JSONResponse:
    """Prove access to one provider League Season, then grant this user access to it.

    Failures never reveal whether the League already exists in Fantasy What-If.
    """

    try:
        # Rejected before any write, throttle included.
        if payload.provider != ESPN_PROVIDER:
            raise unsupported_provider()

        private_access = (
            private_access_from_values(
                payload.credentials.swid.get_secret_value(),
                payload.credentials.espn_s2.get_secret_value(),
            )
            if payload.credentials is not None
            else None
        )

        result = import_league(
            SessionFactory,
            user_id=context.user_id,
            session_id=context.session_id,
            source=resolve_client_ip(request),
            provider_league_id=payload.provider_league_id,
            season_year=payload.season_year,
            private_access=private_access,
            credential_keyring=settings.provider_credential_keyring(),
            throttle_secret=settings.app_secret_key.get_secret_value(),
            user_maximum=settings.provider_validation_workspace_maximum_requests,
            source_maximum=settings.provider_validation_source_maximum_requests,
            window_minutes=settings.provider_validation_window_minutes,
            http_get=http_get,
        )
    except ProviderOnboardingError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            headers=(
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds is not None
                else None
            ),
            content={"detail": exc.detail, "code": exc.code, "retryable": exc.retryable},
        )

    return LeagueImportResponse(
        provider=ESPN_PROVIDER,
        provider_league_id=result.provider_league_id,
        league_name=result.league_name,
        season_id=result.season_id,
        season_year=result.season_year,
        access_status="active",
        sync_state=result.sync_state,
        data_available=result.data_available,
        sync_queued=result.sync_queued,
    )
