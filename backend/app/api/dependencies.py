from typing import Annotated

from fastapi import HTTPException, Query, status

from app.analytics.optimal_lineup import (
    TeamOptimalLineupSummary,
)
from app.db.session import SessionFactory
from app.domain.league import LeagueSnapshot
from app.domain.lineup import TeamWeekRoster
from app.repositories.league_repository import (
    load_league_snapshot,
)
from app.repositories.optimal_lineup_repository import (
    load_optimal_lineup_summaries,
)
from app.repositories.player_week_repository import (
    load_team_week_rosters,
)
from app.services.active_season_context import (
    ViewSeasonResolutionError,
    resolve_view_season_context,
)

_STORED_DATA_UNAVAILABLE = "League data has not been synchronized yet."


def get_current_league_snapshot(
    season_id: Annotated[int | None, Query(gt=0)] = None,
) -> LeagueSnapshot:
    try:
        with SessionFactory() as session:
            context = resolve_view_season_context(
                session=session,
                requested_season_id=season_id,
            )

            snapshot = load_league_snapshot(
                session,
                season_id=context.season_id,
            )
    except ViewSeasonResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve stored league data.",
        ) from exc

    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_STORED_DATA_UNAVAILABLE,
        )

    return snapshot


def get_completed_team_week_rosters(
    season_id: Annotated[int | None, Query(gt=0)] = None,
) -> tuple[TeamWeekRoster, ...]:
    try:
        with SessionFactory() as session:
            context = resolve_view_season_context(
                session=session,
                requested_season_id=season_id,
            )

            snapshot = load_league_snapshot(
                session,
                season_id=context.season_id,
            )

            if snapshot is None:
                raise HTTPException(
                    status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
                    detail=_STORED_DATA_UNAVAILABLE,
                )

            return load_team_week_rosters(
                session,
                season_id=context.season_id,
                through_week=snapshot.current_week - 1,
            )
    except HTTPException:
        raise
    except ViewSeasonResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=("Unable to retrieve stored player-week data."),
        ) from exc


def get_persisted_optimal_lineup_summaries(
    season_id: Annotated[int | None, Query(gt=0)] = None,
) -> tuple[TeamOptimalLineupSummary, ...]:
    """Load the selected season's stored optimal lineups."""

    try:
        with SessionFactory() as session:
            context = resolve_view_season_context(
                session=session,
                requested_season_id=season_id,
            )

            summaries = load_optimal_lineup_summaries(
                session,
                season_id=context.season_id,
            )
    except ViewSeasonResolutionError as exc:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=("Unable to retrieve stored optimal-lineup data."),
        ) from exc

    if not summaries:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Optimal-lineup analysis has not been generated yet."),
        )

    return summaries
