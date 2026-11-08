"""Resolve the active league and season for stored-data reads."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import League, Season
from app.db.platform_enums import InstallationStatus
from app.db.platform_models import Installation


class ActiveSeasonResolutionError(RuntimeError):
    """The active league and season could not be resolved safely."""


@dataclass(frozen=True, slots=True)
class ActiveSeasonContext:
    """Internal database identities for the active league and season."""

    installation_id: int
    league_id: int
    season_id: int


def resolve_active_season_context(
    *,
    session: Session,
) -> ActiveSeasonContext:
    """Resolve the single active installation's league and season.

    Stored-data reads do not require an enabled provider connection.
    They require only a valid active installation, league, and season.
    """

    installations = session.scalars(select(Installation).order_by(Installation.id)).all()

    active_installations = [
        installation
        for installation in installations
        if installation.status is InstallationStatus.ACTIVE
    ]

    if not active_installations:
        raise ActiveSeasonResolutionError("No active installation is configured.")

    if len(active_installations) > 1:
        raise ActiveSeasonResolutionError("Multiple active installations are configured.")

    installation = active_installations[0]

    if installation.active_league_id is None:
        raise ActiveSeasonResolutionError("The active installation does not have an active league.")

    if installation.active_season_id is None:
        raise ActiveSeasonResolutionError("The active installation does not have an active season.")

    league = session.get(
        League,
        installation.active_league_id,
    )

    if league is None:
        raise ActiveSeasonResolutionError("The active league does not exist.")

    season = session.get(
        Season,
        installation.active_season_id,
    )

    if season is None:
        raise ActiveSeasonResolutionError("The active season does not exist.")

    if season.league_id != league.id:
        raise ActiveSeasonResolutionError("The active season does not belong to the active league.")

    return ActiveSeasonContext(
        installation_id=installation.id,
        league_id=league.id,
        season_id=season.id,
    )
