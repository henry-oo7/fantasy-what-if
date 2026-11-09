"""Public first-run setup endpoints."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.security import (
    require_pre_auth_csrf,
    validate_request_origin,
)
from app.auth.security import (
    InvalidPasswordError,
    InvalidUsernameError,
)
from app.db.session import SessionFactory
from app.repositories.auth_repository import (
    DuplicateEmailError,
    DuplicateUsernameError,
)
from app.schemas.setup import (
    BootstrapAdminRequest,
    BootstrapAdminResponse,
    SetupStatusResponse,
)
from app.services.setup_bootstrap import (
    SetupBootstrapStateError,
    SetupBootstrapUnavailableError,
    bootstrap_administrator,
)
from app.services.setup_status import (
    SetupStateError,
    resolve_setup_state,
)

router = APIRouter(
    prefix="/api/setup",
    tags=["setup"],
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
