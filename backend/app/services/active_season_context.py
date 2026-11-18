"""Resolve synchronization and viewing season contexts."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import League, Season
from app.db.platform_enums import (
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import Installation


class ActiveSeasonResolutionError(RuntimeError):
    """A synchronization-active season could not be resolved safely."""


class ViewSeasonResolutionError(RuntimeError):
    """A season available for stored-data viewing could not be resolved."""


@dataclass(frozen=True, slots=True)
class ActiveSeasonContext:
    """Internal identities for one synchronization-active season."""

    installation_id: int
    league_id: int
    season_id: int


@dataclass(frozen=True, slots=True)
class ViewSeasonContext:
    """Internal identities for one season selected for stored-data reads."""

    installation_id: int
    league_id: int
    season_id: int
    is_synchronization_active: bool


def _resolve_active_workspace(
    session: Session,
    *,
    workspace_id: int,
    error_type: type[RuntimeError],
) -> Installation:
    workspace = session.get(
        Installation,
        workspace_id,
    )

    if workspace is None or workspace.status is not InstallationStatus.ACTIVE:
        raise error_type("The requested workspace is not active.")

    return workspace


def resolve_active_season_context(
    *,
    session: Session,
    workspace_id: int,
) -> ActiveSeasonContext:
    """Resolve the only season eligible for synchronization."""

    installation = _resolve_active_workspace(
        session,
        workspace_id=workspace_id,
        error_type=ActiveSeasonResolutionError,
    )

    if installation.active_league_id is None:
        raise ActiveSeasonResolutionError("The active installation does not have an active league.")

    if installation.active_season_id is None:
        raise ActiveSeasonResolutionError("The active installation does not have an active season.")

    league = session.get(
        League,
        installation.active_league_id,
    )

    if league is None or league.installation_id != installation.id:
        raise ActiveSeasonResolutionError(
            "The active league does not belong to the requested workspace."
        )

    season = session.get(
        Season,
        installation.active_season_id,
    )

    if season is None:
        raise ActiveSeasonResolutionError("The active season does not exist.")

    if season.league_id != league.id:
        raise ActiveSeasonResolutionError("The active season does not belong to the active league.")

    if season.status is not SeasonStatus.ACTIVE:
        raise ActiveSeasonResolutionError("The synchronization season is not active.")

    return ActiveSeasonContext(
        installation_id=installation.id,
        league_id=league.id,
        season_id=season.id,
    )


def resolve_view_season_context(
    *,
    session: Session,
    workspace_id: int,
    requested_season_id: int | None = None,
) -> ViewSeasonContext:
    """Resolve an imported season for stored-data reads.

    An explicit imported season wins. Otherwise, the synchronization-active
    season is used. A historical-only installation falls back to its newest
    imported season.
    """

    installation = _resolve_active_workspace(
        session,
        workspace_id=workspace_id,
        error_type=ViewSeasonResolutionError,
    )

    if requested_season_id is not None:
        season = session.get(
            Season,
            requested_season_id,
        )

        if season is None:
            raise ViewSeasonResolutionError("The requested season does not exist.")

        league = session.get(
            League,
            season.league_id,
        )

        if league is None or league.installation_id != installation.id:
            raise ViewSeasonResolutionError(
                "The requested season does not belong to this installation."
            )
    elif installation.active_season_id is not None:
        season = session.get(
            Season,
            installation.active_season_id,
        )

        if season is None:
            raise ViewSeasonResolutionError("The active season does not exist.")

        league = session.get(
            League,
            season.league_id,
        )

        if league is None or league.installation_id != installation.id:
            raise ViewSeasonResolutionError(
                "The active season does not belong to the requested workspace."
            )
    else:
        result = session.execute(
            select(
                Season,
                League,
            )
            .join(
                League,
                League.id == Season.league_id,
            )
            .where(
                League.installation_id == installation.id,
            )
            .order_by(
                Season.year.desc(),
                Season.id.desc(),
            )
            .limit(1)
        ).first()

        if result is None:
            raise ViewSeasonResolutionError("No imported season is available to view.")

        season, league = result

    return ViewSeasonContext(
        installation_id=installation.id,
        league_id=league.id,
        season_id=season.id,
        is_synchronization_active=(
            season.id == installation.active_season_id and season.status is SeasonStatus.ACTIVE
        ),
    )
