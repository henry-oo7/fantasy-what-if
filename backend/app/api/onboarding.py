"""Provisional-administrator onboarding endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import (
    AuthenticationContext,
    WorkspaceAccessError,
    get_authentication_context,
    require_session_csrf,
)
from app.api.registration import normalize_request_source
from app.api.security import validate_request_origin
from app.auth.cookies import clear_authentication_cookies
from app.core.settings import Settings, get_settings
from app.db.session import SessionFactory
from app.providers.espn.league_access import HttpGet
from app.schemas.onboarding import (
    InitialSyncJobResponse,
    InitialSyncProgressResponse,
    InitialSyncStartRequest,
    OnboardingSpectatorResponse,
    ProviderClaimRequest,
    ProviderClaimResponse,
    ProviderTargetResponse,
    ProviderValidationRequest,
    ProviderValidationResponse,
    SpectatorSetupRequest,
)
from app.services.league_claim import (
    LEAGUE_ALREADY_CLAIMED_DETAIL,
    LeagueAlreadyClaimedError,
    ValidatedTarget,
    claim_validated_league,
    read_validated_target,
)
from app.services.onboarding_initial_sync import (
    InitialSyncProgress,
    read_initial_sync_progress,
    start_initial_sync,
)
from app.services.onboarding_spectator import (
    OnboardingSpectatorState,
    read_onboarding_spectator,
    set_up_onboarding_spectator,
)
from app.services.provider_onboarding import (
    ProviderOnboardingError,
    ProviderWorkspaceAccessError,
    private_access_from_values,
    run_provider_validation,
    unsupported_provider,
)
from app.services.spectator_account import (
    SpectatorAlreadyExistsError,
    SpectatorPasswordError,
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


def _error_response(exc: ProviderOnboardingError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code, "retryable": exc.retryable},
    )


def _target_response(target: ValidatedTarget) -> ProviderTargetResponse:
    return ProviderTargetResponse(
        provider=target.provider.value,
        league_id=target.league_id,
        season_year=target.season_year,
        access_mode=target.access_mode,
    )


@router.get("/provider/target", response_model=ProviderTargetResponse)
def read_provider_target(
    context: Annotated[AuthenticationContext, Depends(get_authentication_context)],
) -> ProviderTargetResponse | JSONResponse:
    """Return the safe validated target awaiting claim confirmation.

    Never includes credentials, ciphertext, key versions or request metadata.
    """

    try:
        with SessionFactory() as session:
            target = read_validated_target(
                session, user_id=context.user_id, workspace_id=context.workspace_id
            )
    except ProviderWorkspaceAccessError as exc:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.", code=exc.code
        ) from exc
    except ProviderOnboardingError as exc:
        return _error_response(exc)

    return _target_response(target)


@router.post("/provider/claim", response_model=ProviderClaimResponse)
def claim_provider_league(
    context: Annotated[AuthenticationContext, Depends(require_session_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(validate_request_origin)],
    payload: ProviderClaimRequest | None = None,
) -> ProviderClaimResponse | JSONResponse:
    """Connect the validated league to this workspace.

    A duplicate claim deletes the losing provisional registration, clears
    the authentication cookies and returns 409 without disclosing anything
    about the existing workspace.
    """

    del payload

    try:
        with SessionFactory() as session:
            target = claim_validated_league(
                session, user_id=context.user_id, workspace_id=context.workspace_id
            )
    except ProviderWorkspaceAccessError as exc:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.", code=exc.code
        ) from exc
    except ProviderOnboardingError as exc:
        return _error_response(exc)
    except LeagueAlreadyClaimedError:
        response = JSONResponse(
            status_code=409,
            content={
                "detail": LEAGUE_ALREADY_CLAIMED_DETAIL,
                "code": "provider_league_already_claimed",
                "retryable": False,
            },
        )
        clear_authentication_cookies(response, settings=settings)

        return response

    return ProviderClaimResponse(
        status="claimed",
        code="provider_league_claimed",
        onboarding_stage="initial_sync_pending",
        target=_target_response(target),
    )


def _progress_response(progress: InitialSyncProgress) -> InitialSyncProgressResponse:
    job = progress.job

    return InitialSyncProgressResponse(
        onboarding_stage=progress.onboarding_stage,
        job=(
            InitialSyncJobResponse(
                id=job.id,
                status=job.status,
                retryable=job.retryable,
                detail=job.detail,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                failed_at=job.failed_at,
            )
            if job is not None
            else None
        ),
    )


@router.post("/synchronization", response_model=InitialSyncProgressResponse)
def start_onboarding_synchronization(
    context: Annotated[AuthenticationContext, Depends(require_session_csrf)],
    _: Annotated[None, Depends(validate_request_origin)],
    payload: InitialSyncStartRequest | None = None,
) -> InitialSyncProgressResponse | JSONResponse:
    """Queue the first import, or return the active one. Never calls ESPN."""

    del payload

    try:
        with SessionFactory() as session:
            progress = start_initial_sync(
                session, user_id=context.user_id, workspace_id=context.workspace_id
            )
    except ProviderWorkspaceAccessError as exc:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.", code=exc.code
        ) from exc
    except ProviderOnboardingError as exc:
        return _error_response(exc)

    return _progress_response(progress)


@router.get("/synchronization", response_model=InitialSyncProgressResponse)
def read_onboarding_synchronization(
    context: Annotated[AuthenticationContext, Depends(get_authentication_context)],
) -> InitialSyncProgressResponse | JSONResponse:
    """Safe progress of this workspace's latest first-import job."""

    try:
        with SessionFactory() as session:
            progress = read_initial_sync_progress(
                session, user_id=context.user_id, workspace_id=context.workspace_id
            )
    except ProviderWorkspaceAccessError as exc:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.", code=exc.code
        ) from exc
    except ProviderOnboardingError as exc:
        return _error_response(exc)

    return _progress_response(progress)


def _spectator_response(state: OnboardingSpectatorState) -> OnboardingSpectatorResponse:
    return OnboardingSpectatorResponse(
        workspace_slug=state.access.workspace_slug,
        configured=state.access.configured,
        enabled=state.access.enabled,
        onboarding_stage=state.onboarding_stage,
    )


@router.get("/spectator", response_model=OnboardingSpectatorResponse)
def read_onboarding_spectator_access(
    context: Annotated[AuthenticationContext, Depends(get_authentication_context)],
) -> OnboardingSpectatorResponse | JSONResponse:
    """Safe shared spectator state during onboarding."""

    try:
        with SessionFactory() as session:
            state = read_onboarding_spectator(
                session, user_id=context.user_id, workspace_id=context.workspace_id
            )
    except ProviderWorkspaceAccessError as exc:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.", code=exc.code
        ) from exc
    except ProviderOnboardingError as exc:
        return _error_response(exc)

    return _spectator_response(state)


@router.post("/spectator", response_model=OnboardingSpectatorResponse)
def set_up_onboarding_spectator_access(
    payload: SpectatorSetupRequest,
    context: Annotated[AuthenticationContext, Depends(require_session_csrf)],
    _: Annotated[None, Depends(validate_request_origin)],
) -> OnboardingSpectatorResponse | JSONResponse:
    """Create the shared spectator and complete onboarding. Never activates."""

    try:
        with SessionFactory() as session:
            state = set_up_onboarding_spectator(
                session,
                user_id=context.user_id,
                workspace_id=context.workspace_id,
                password=payload.password,
            )
    except ProviderWorkspaceAccessError as exc:
        raise WorkspaceAccessError(
            detail="Workspace access is unavailable.", code=exc.code
        ) from exc
    except ProviderOnboardingError as exc:
        return _error_response(exc)
    except SpectatorAlreadyExistsError:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Shared spectator access is already configured.",
                "code": "spectator_already_configured",
                "retryable": False,
            },
        )
    except SpectatorPasswordError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "code": "spectator_password_invalid", "retryable": False},
        )

    return _spectator_response(state)
