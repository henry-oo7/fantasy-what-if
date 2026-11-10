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


class SeasonImporter(Protocol):
    """Callable that imports another provider season."""

    def __call__(
        self,
        *,
        season_year: int,
    ): ...


def import_season(
    *,
    season_year: int,
):
    """Import another season using configured credentials."""

    from app.providers.credential_vault import (
        ProviderCredentialConfigurationError,
    )
    from app.services.season_import import (
        import_provider_season,
    )

    settings = get_settings()
    key_setting = settings.provider_credential_key

    if key_setting is None:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    with SessionFactory() as session:
        return import_provider_season(
            session,
            season_year=season_year,
            credential_key=(key_setting.get_secret_value()),
        )


def get_season_importer() -> SeasonImporter:
    """Return the production season importer."""

    return import_season
