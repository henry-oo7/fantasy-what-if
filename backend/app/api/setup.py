"""Public first-run setup endpoints."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.auth_dependencies import (
    AuthenticationContext,
    require_admin_user,
    require_session_csrf,
)
from app.api.security import (
    require_pre_auth_csrf,
    validate_request_origin,
)
from app.api.setup_dependencies import (
    SetupFinalizer,
    SetupProviderConnectionConfigurator,
    get_setup_finalizer,
    get_setup_provider_configurator,
)
from app.auth.security import (
    InvalidPasswordError,
    InvalidUsernameError,
)
from app.db.auth_models import User
from app.db.platform_enums import FantasyProvider
from app.db.session import SessionFactory
from app.providers.credential_vault import (
    ProviderCredentialConfigurationError,
)
from app.providers.domain import ProviderCredentials
from app.repositories.auth_repository import (
    DuplicateEmailError,
    DuplicateUsernameError,
)
from app.schemas.setup import (
    BootstrapAdminRequest,
    BootstrapAdminResponse,
    SetupFinalizationResponse,
    SetupProviderConnectionRequest,
    SetupProviderConnectionResponse,
    SetupProviderResponse,
    SetupProvidersResponse,
    SetupStatusResponse,
)
from app.services.setup_bootstrap import (
    SetupBootstrapStateError,
    SetupBootstrapUnavailableError,
    bootstrap_administrator,
)
from app.services.setup_finalization import (
    SetupFinalizationUnavailableError,
    SetupFinalizationValidationError,
)
from app.services.setup_provider_connection import (
    SetupProviderAlreadyConfiguredError,
    SetupProviderConnectionUnavailableError,
    SetupProviderValidationError,
)
from app.services.setup_providers import (
    list_setup_providers,
)
from app.services.setup_status import (
    SetupStateError,
    resolve_setup_state,
)

router = APIRouter(
    prefix="/api/setup",
    tags=["setup"],
)


@router.get("/providers")
def read_setup_providers() -> SetupProvidersResponse:
    """Return safe metadata for known fantasy providers."""

    return SetupProvidersResponse(
        providers=tuple(
            SetupProviderResponse(
                provider=provider.provider.value,
                display_name=provider.display_name,
                implemented=provider.implemented,
                requires_credentials=(provider.requires_credentials),
                credential_fields=(provider.credential_fields),
            )
            for provider in list_setup_providers()
        )
    )


@router.get("/status")
def read_setup_status() -> SetupStatusResponse:
    """Return public lifecycle state for frontend routing."""

    try:
        with SessionFactory() as session:
            setup_state = resolve_setup_state(session)
    except SetupStateError as exc:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail="Installation configuration is invalid.",
        ) from exc

    return SetupStatusResponse(
        status=setup_state.status.value,
        installation_exists=(setup_state.installation_exists),
        bootstrap_available=(setup_state.bootstrap_available),
    )


@router.post("/bootstrap-admin")
def bootstrap_admin(
    payload: BootstrapAdminRequest,
    _: Annotated[
        None,
        Depends(validate_request_origin),
    ],
    __: Annotated[
        None,
        Depends(require_pre_auth_csrf),
    ],
) -> BootstrapAdminResponse:
    """Create the one allowed first administrator account."""

    try:
        with SessionFactory() as session:
            installation, administrator = bootstrap_administrator(
                session,
                username=payload.username,
                display_name=payload.display_name,
                email=payload.email,
                password=payload.password,
            )

            response = BootstrapAdminResponse(
                installation_status=(installation.status.value),
                administrator_id=administrator.id,
                username=administrator.username,
                display_name=administrator.display_name,
                email=administrator.email,
            )

            session.commit()

            return response

    except SetupBootstrapUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Administrator bootstrap is no longer available."),
        ) from exc
    except SetupBootstrapStateError as exc:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail="Installation configuration is invalid.",
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


@router.post("/provider-connection")
def create_setup_provider_connection(
    payload: SetupProviderConnectionRequest,
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
    configurator: Annotated[
        SetupProviderConnectionConfigurator,
        Depends(get_setup_provider_configurator),
    ],
) -> SetupProviderConnectionResponse:
    """Validate and store one encrypted provider connection."""

    provider = FantasyProvider(payload.provider)

    credentials = ProviderCredentials(
        values={
            key: value
            for key, value in {
                "league_id": payload.league_id,
                "season": payload.season,
                "espn_s2": payload.espn_s2,
                "swid": payload.swid,
            }.items()
            if value is not None and value.strip()
        }
    )

    try:
        connection = configurator(
            provider=provider,
            display_name=payload.display_name,
            credentials=credentials,
        )
    except SetupProviderAlreadyConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SetupProviderConnectionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SetupProviderValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except ProviderCredentialConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Provider credential encryption is not configured."),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    return SetupProviderConnectionResponse(
        connection_id=connection.id,
        provider=connection.provider.value,
        display_name=connection.display_name,
        credential_version=connection.credential_version,
        is_active=connection.is_active,
        validated=(
            connection.last_validated_at is not None and connection.last_validation_error is None
        ),
    )


@router.post("/finalize")
def finalize_setup_installation(
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
    finalizer: Annotated[
        SetupFinalizer,
        Depends(get_setup_finalizer),
    ],
) -> SetupFinalizationResponse:
    """Synchronize and activate the configured installation."""

    try:
        result = finalizer()
    except SetupFinalizationUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SetupFinalizationValidationError as exc:
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
            detail=("Unable to complete the initial provider synchronization."),
        ) from exc

    sync_result = result.sync_result

    return SetupFinalizationResponse(
        installation_status="active",
        installation_id=result.installation_id,
        league_id=result.league_id,
        season_id=result.season_id,
        provider=sync_result.provider.value,
        provider_league_id=(sync_result.provider_league_id),
        provider_season_id=(sync_result.provider_season_id),
        current_week=sync_result.current_week,
        full_sync=sync_result.full_sync,
        teams_upserted=sync_result.teams_upserted,
        matchups_upserted=(sync_result.matchups_upserted),
        fantasy_byes_upserted=(sync_result.fantasy_byes_upserted),
        player_week_scores_upserted=(sync_result.player_week_scores_upserted),
    )
