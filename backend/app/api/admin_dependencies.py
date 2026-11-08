from typing import Protocol

from app.core.settings import get_settings
from app.db.session import SessionFactory
from app.services.active_provider_sync import (
    synchronize_active_season,
)
from app.services.provider_sync import ProviderSyncResult


class SynchronizationRunner(Protocol):
    def __call__(
        self,
        *,
        full_sync: bool,
    ) -> ProviderSyncResult: ...


def run_synchronization(
    *,
    full_sync: bool,
) -> ProviderSyncResult:
    settings = get_settings()

    with SessionFactory() as session:
        return synchronize_active_season(
            session=session,
            settings=settings,
            full_sync=full_sync,
        )


def get_synchronization_runner() -> SynchronizationRunner:
    return run_synchronization
