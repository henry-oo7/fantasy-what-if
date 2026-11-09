"""Dependency-injected setup operations."""

from typing import Protocol

from app.core.settings import get_settings
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import ProviderConnection
from app.db.session import SessionFactory
from app.providers.credential_vault import (
    ProviderCredentialConfigurationError,
)
from app.providers.domain import ProviderCredentials
from app.services.setup_finalization import (
    SetupFinalizationResult,
    finalize_installation_setup,
)
from app.services.setup_provider_connection import (
    configure_provider_connection,
)


class SetupProviderConnectionConfigurator(Protocol):
    """Callable that validates and stores a provider connection."""

    def __call__(
        self,
        *,
        provider: FantasyProvider,
        display_name: str,
        credentials: ProviderCredentials,
    ) -> ProviderConnection: ...


def configure_setup_provider_connection(
    *,
    provider: FantasyProvider,
    display_name: str,
    credentials: ProviderCredentials,
) -> ProviderConnection:
    """Run provider configuration with application settings."""

    settings = get_settings()
    key_setting = settings.provider_credential_key

    if key_setting is None:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    with SessionFactory() as session:
        connection = configure_provider_connection(
            session,
            provider=provider,
            display_name=display_name,
            credentials=credentials,
            credential_key=(key_setting.get_secret_value()),
        )

        session.commit()

        return connection


def get_setup_provider_configurator() -> SetupProviderConnectionConfigurator:
    """Return the production provider configurator."""

    return configure_setup_provider_connection


class SetupFinalizer(Protocol):
    """Callable that completes installation setup."""

    def __call__(self) -> SetupFinalizationResult: ...


def finalize_setup() -> SetupFinalizationResult:
    """Finalize setup using configured credential encryption."""

    settings = get_settings()
    key_setting = settings.provider_credential_key

    if key_setting is None:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    with SessionFactory() as session:
        return finalize_installation_setup(
            session,
            credential_key=(key_setting.get_secret_value()),
        )


def get_setup_finalizer() -> SetupFinalizer:
    """Return the production setup finalizer."""

    return finalize_setup
