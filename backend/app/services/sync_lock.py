from sqlalchemy import text
from sqlalchemy.orm import Session


class SyncAlreadyRunningError(RuntimeError):
    pass


PROVIDER_SYNC_LOCK_NAMESPACE = 1_179_921_927


def acquire_season_sync_lock(
    session: Session,
    *,
    season_id: int,
) -> None:
    """Acquire a transaction-scoped lock for one internal season."""

    if season_id < 1:
        raise ValueError("season_id must be positive.")

    acquired = session.scalar(
        text(
            """
            SELECT pg_try_advisory_xact_lock(
                :namespace,
                :season_id
            )
            """
        ),
        {
            "namespace": (PROVIDER_SYNC_LOCK_NAMESPACE),
            "season_id": season_id,
        },
    )

    if not acquired:
        raise SyncAlreadyRunningError(
            f"Another synchronization is already running for season {season_id}."
        )
