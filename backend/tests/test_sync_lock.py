import pytest

from app.db.session import SessionFactory
from app.services.sync_lock import (
    SyncAlreadyRunningError,
    acquire_sync_lock,
)

LEAGUE_ID = 529250970
SEASON = 2026


def test_sync_lock_can_be_acquired() -> None:
    with SessionFactory() as session:
        acquire_sync_lock(
            session,
            espn_league_id=LEAGUE_ID,
            season=SEASON,
        )

        session.rollback()


def test_same_sync_lock_cannot_be_acquired_twice() -> None:
    with (
        SessionFactory() as first_session,
        SessionFactory() as second_session,
    ):
        acquire_sync_lock(
            first_session,
            espn_league_id=LEAGUE_ID,
            season=SEASON,
        )

        with pytest.raises(
            SyncAlreadyRunningError,
            match="already running",
        ):
            acquire_sync_lock(
                second_session,
                espn_league_id=LEAGUE_ID,
                season=SEASON,
            )

        first_session.rollback()
        second_session.rollback()


def test_sync_lock_is_released_after_rollback() -> None:
    with SessionFactory() as first_session:
        acquire_sync_lock(
            first_session,
            espn_league_id=LEAGUE_ID,
            season=SEASON,
        )

        first_session.rollback()

    with SessionFactory() as second_session:
        acquire_sync_lock(
            second_session,
            espn_league_id=LEAGUE_ID,
            season=SEASON,
        )

        second_session.rollback()


def test_different_seasons_use_different_locks() -> None:
    with (
        SessionFactory() as first_session,
        SessionFactory() as second_session,
    ):
        acquire_sync_lock(
            first_session,
            espn_league_id=LEAGUE_ID,
            season=2026,
        )

        acquire_sync_lock(
            second_session,
            espn_league_id=LEAGUE_ID,
            season=2025,
        )

        first_session.rollback()
        second_session.rollback()
