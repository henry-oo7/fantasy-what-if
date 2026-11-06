from fastapi import HTTPException, status

from app.core.settings import get_settings
from app.db.session import SessionFactory
from app.domain.league import LeagueSnapshot
from app.repositories.league_repository import load_league_snapshot


def get_current_league_snapshot() -> LeagueSnapshot:
    settings = get_settings()

    try:
        with SessionFactory() as session:
            snapshot = load_league_snapshot(
                session,
                espn_league_id=settings.espn_league_id,
                year=settings.espn_season,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve stored league data.",
        ) from exc

    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "League data has not been synchronized yet. Run the ESPN synchronization command."
            ),
        )

    return snapshot
