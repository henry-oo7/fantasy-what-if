from fastapi import HTTPException, status

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
    ActiveSeasonResolutionError,
    resolve_active_season_context,
)

_STORED_DATA_UNAVAILABLE = "League data has not been synchronized yet."


def get_current_league_snapshot() -> LeagueSnapshot:
    try:
        with SessionFactory() as session:
            context = resolve_active_season_context(
                session=session,
            )

            snapshot = load_league_snapshot(
                session,
                season_id=context.season_id,
            )
    except ActiveSeasonResolutionError as exc:
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


def get_completed_team_week_rosters() -> tuple[TeamWeekRoster, ...]:
    try:
        with SessionFactory() as session:
            context = resolve_active_season_context(
                session=session,
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
    except ActiveSeasonResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=("Unable to retrieve stored player-week data."),
        ) from exc


def get_persisted_optimal_lineup_summaries() -> tuple[TeamOptimalLineupSummary, ...]:
    """Load the active season's stored optimal lineups."""

    try:
        with SessionFactory() as session:
            context = resolve_active_season_context(
                session=session,
            )

            summaries = load_optimal_lineup_summaries(
                session,
                season_id=context.season_id,
            )
    except ActiveSeasonResolutionError as exc:
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
