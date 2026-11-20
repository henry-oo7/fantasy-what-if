from typing import Protocol

from app.core.settings import get_settings
from app.db.session import SessionFactory
from app.providers.domain import ProviderCredentials
from app.services.admin_synchronization_jobs import (
    AdministratorSynchronizationJob,
    enqueue_administrator_synchronization,
)
from app.services.provider_credential_replacement import (
    ProviderCredentialReplacementResult,
    replace_provider_credentials,
)


class SynchronizationJobEnqueuer(Protocol):
    def __call__(
        self,
        *,
        workspace_id: int,
        full_sync: bool,
    ) -> AdministratorSynchronizationJob: ...


def enqueue_synchronization_job(
    *,
    workspace_id: int,
    full_sync: bool,
) -> AdministratorSynchronizationJob:
    with SessionFactory() as session:
        accepted = enqueue_administrator_synchronization(
            session,
            workspace_id=workspace_id,
            full_sync=full_sync,
        )
        session.commit()

    return accepted


def get_synchronization_job_enqueuer() -> SynchronizationJobEnqueuer:
    return enqueue_synchronization_job


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


class BackgroundJobStatusReader(Protocol):
    """Read one safe workspace-owned job status."""

    def __call__(
        self,
        *,
        job_id: int,
        workspace_id: int,
    ): ...


def read_background_job_status(
    *,
    job_id: int,
    workspace_id: int,
):
    """Read safe job state through a short database session."""

    from app.services.background_job_status import (
        get_background_job_status,
    )

    with SessionFactory() as session:
        return get_background_job_status(
            session,
            job_id=job_id,
            workspace_id=workspace_id,
        )


def get_background_job_status_reader() -> BackgroundJobStatusReader:
    """Return the production workspace-scoped job reader."""

    return read_background_job_status


class ProviderCredentialReplacer(Protocol):
    """Replace one workspace's active provider credentials."""

    def __call__(
        self,
        *,
        workspace_id: int,
        credentials: ProviderCredentials,
    ) -> ProviderCredentialReplacementResult: ...


def replace_workspace_provider_credentials(
    *,
    workspace_id: int,
    credentials: ProviderCredentials,
) -> ProviderCredentialReplacementResult:
    """Replace credentials using protected application settings."""

    from app.providers.credential_vault import (
        ProviderCredentialConfigurationError,
    )

    settings = get_settings()
    key_setting = settings.provider_credential_key

    if key_setting is None:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    with SessionFactory() as session:
        result = replace_provider_credentials(
            session,
            workspace_id=workspace_id,
            submitted_credentials=credentials,
            credential_key=key_setting.get_secret_value(),
        )
        session.commit()
        return result


def get_provider_credential_replacer() -> ProviderCredentialReplacer:
    """Return the production provider credential replacer."""

    return replace_workspace_provider_credentials
