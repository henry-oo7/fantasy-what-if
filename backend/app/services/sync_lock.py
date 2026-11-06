from sqlalchemy import text
from sqlalchemy.orm import Session


class SyncAlreadyRunningError(RuntimeError):
    pass


def acquire_sync_lock(
    session: Session,
    *,
    espn_league_id: int,
    season: int,
) -> None:
    acquired = session.scalar(
        text(
            """
            SELECT pg_try_advisory_xact_lock(
                :espn_league_id,
                :season
            )
            """
        ),
        {
            "espn_league_id": espn_league_id,
            "season": season,
        },
    )

    if not acquired:
        raise SyncAlreadyRunningError(
            "Another synchronization is already running for this league and season."
        )
