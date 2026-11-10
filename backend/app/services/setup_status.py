"""Resolve the public installation setup state."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.auth_models import (
    User,
    UserRole,
)
from app.db.models import League, Season
from app.db.platform_enums import InstallationStatus
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)


class SetupStateError(RuntimeError):
    """The installation setup state is structurally invalid."""


@dataclass(frozen=True, slots=True)
class SetupState:
    """Safe lifecycle progress exposed to the frontend."""

    status: InstallationStatus
    installation_exists: bool
    bootstrap_available: bool
    administrator_configured: bool
    provider_configured: bool
    spectator_configured: bool
    active_league_configured: bool


def resolve_setup_state(
    session: Session,
) -> SetupState:
    """Resolve setup progress without exposing private data."""

    installations = session.scalars(select(Installation).order_by(Installation.id)).all()

    if len(installations) > 1:
        raise SetupStateError("Multiple installations are configured.")

    user_count = int(session.scalar(select(func.count()).select_from(User)) or 0)

    administrator_configured = user_count > 0

    if not installations:
        return SetupState(
            status=InstallationStatus.SETUP_REQUIRED,
            installation_exists=False,
            bootstrap_available=(not administrator_configured),
            administrator_configured=(administrator_configured),
            provider_configured=False,
            spectator_configured=False,
            active_league_configured=False,
        )

    installation = installations[0]

    provider_count = int(
        session.scalar(
            select(func.count())
            .select_from(ProviderConnection)
            .where(ProviderConnection.installation_id == installation.id)
        )
        or 0
    )

    provider_configured = provider_count > 0

    spectator_count = int(
        session.scalar(
            select(func.count()).select_from(User).where(User.role == UserRole.SPECTATOR)
        )
        or 0
    )

    if spectator_count > 1:
        raise SetupStateError("Multiple spectator accounts are configured.")

    spectator_configured = spectator_count == 1

    imported_season_count = int(
        session.scalar(
            select(func.count())
            .select_from(Season)
            .join(
                League,
                League.id == Season.league_id,
            )
            .where(
                League.installation_id == installation.id,
            )
        )
        or 0
    )

    active_league_configured = (
        installation.active_league_id is not None and imported_season_count > 0
    )

    bootstrap_available = (
        not administrator_configured and installation.status is not InstallationStatus.ACTIVE
    )

    return SetupState(
        status=installation.status,
        installation_exists=True,
        bootstrap_available=bootstrap_available,
        administrator_configured=(administrator_configured),
        provider_configured=provider_configured,
        spectator_configured=spectator_configured,
        active_league_configured=(active_league_configured),
    )
