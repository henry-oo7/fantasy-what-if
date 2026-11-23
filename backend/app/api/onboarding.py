"""Provisional-administrator onboarding endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import (
    AuthenticationContext,
    WorkspaceAccessError,
    require_session_csrf,
)
from app.api.registration import normalize_request_source
from app.api.security import validate_request_origin
from app.core.settings import Settings, get_settings
from app.db.session import SessionFactory
from app.providers.espn.league_access import HttpGet
from app.schemas.onboarding import ProviderValidationRequest, ProviderValidationResponse
from app.services.provider_onboarding import (
    ProviderOnboardingError,
    ProviderWorkspaceAccessError,
    private_access_from_values,
    run_provider_validation,
    unsupported_provider,
)

router = APIRouter(
    prefix="/api/onboarding",
    tags=["onboarding"],
)


def get_espn_http_get() -> HttpGet | None:
    """Outbound ESPN transport. None selects the real bounded client.

    Tests override this dependency so no live ESPN request is made.
    """

    return None


@router.post("/provider/validate", response_model=ProviderValidationResponse)
def validate_provider(
    payload: ProviderValidationRequest,
    request: Request,
    context: Annotated[AuthenticationContext, Depends(require_session_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
    http_get: Annotated[HttpGet | None, Depends(get_espn_http_get)],
    _: Annotated[None, Depends(validate_request_origin)],
) -> ProviderValidationResponse | JSONResponse:
    """Check that Fantasy What-If can read one ESPN league season.

    Public leagues need no credentials. When ESPN requires authentication
    the response asks for SWID and espn_s2. Success stores the validated
    target and advances onboarding; it never claims or imports the league.
    """

    try:
        if payload.provider != "espn":
            raise unsupported_provider()

        private_access = (
            private_access_from_values(
                payload.credentials.swid.get_secret_value(),
                payload.credentials.espn_s2.get_secret_value(),
            )
            if payload.credentials is not None
            else None
        )

        outcome = run_provider_validation(
            SessionFactory,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            source=normalize_request_source(request.client.host if request.client else None),
            league_id=payload.league_id,
            season_year=payload.season_year,
            private_access=private_access,
            credential_keyring=settings.provider_credential_keyring(),
            throttle_secret=settings.app_secret_key.get_secret_value(),
            workspace_maximum=settings.provider_validation_workspace_maximum_requests,
            source_maximum=settings.provider_validation_source_maximum_requests,
            window_minutes=settings.provider_validation_window_minutes,
            http_get=http_get,
        )
    except ProviderWorkspaceAccessError as exc:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.",
            code=exc.code,
        ) from exc
    except ProviderOnboardingError as exc:
        headers = (
            {"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None
        )

        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content={
                "detail": exc.detail,
                "code": exc.code,
                "retryable": exc.retryable,
            },
        )

    if outcome.credentials_required:
        return ProviderValidationResponse(
            status="credentials_required",
            code="credentials_required",
            provider="espn",
            access_mode=None,
            onboarding_stage=outcome.onboarding_stage,
        )

    return ProviderValidationResponse(
        status="validated",
        code="provider_validated",
        provider="espn",
        access_mode=outcome.access_mode,
        onboarding_stage=outcome.onboarding_stage,
    )
