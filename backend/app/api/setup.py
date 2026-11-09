"""Public first-run setup endpoints."""

from fastapi import (
    APIRouter,
    HTTPException,
    status,
)

from app.db.session import SessionFactory
from app.schemas.setup import SetupStatusResponse
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
