"""Resolve the public installation setup state."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.auth_models import User
from app.db.platform_enums import InstallationStatus
from app.db.platform_models import Installation


class SetupStateError(RuntimeError):
    """The installation setup state is structurally invalid."""


@dataclass(frozen=True, slots=True)
class SetupState:
    """Minimum lifecycle state exposed to the frontend."""

    status: InstallationStatus
    installation_exists: bool
    bootstrap_available: bool


def resolve_setup_state(
    session: Session,
) -> SetupState:
    """Resolve setup routing state without exposing private data."""

    installations = session.scalars(select(Installation).order_by(Installation.id)).all()

    if len(installations) > 1:
        raise SetupStateError("Multiple installations are configured.")

    user_count = int(session.scalar(select(func.count()).select_from(User)) or 0)

    if not installations:
        return SetupState(
            status=InstallationStatus.SETUP_REQUIRED,
            installation_exists=False,
            bootstrap_available=user_count == 0,
        )

    installation = installations[0]

    bootstrap_available = user_count == 0 and installation.status is not InstallationStatus.ACTIVE

    return SetupState(
        status=installation.status,
        installation_exists=True,
        bootstrap_available=bootstrap_available,
    )
