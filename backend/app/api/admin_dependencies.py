from typing import Protocol

from app.core.settings import get_settings
from app.db.session import SessionFactory
from app.services.espn_sync import (
    SyncResult,
    synchronize_espn,
)


class SynchronizationRunner(Protocol):
    def __call__(
        self,
        *,
        full_sync: bool,
    ) -> SyncResult: ...


def run_synchronization(
    *,
    full_sync: bool,
) -> SyncResult:
    settings = get_settings()

    with SessionFactory() as session:
        return synchronize_espn(
            session=session,
            settings=settings,
            full_sync=full_sync,
        )


def get_synchronization_runner() -> SynchronizationRunner:
    return run_synchronization
