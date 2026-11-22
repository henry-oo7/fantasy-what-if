"""Dependency-injected setup operations."""

from typing import Annotated, Protocol

from fastapi import Depends, HTTPException, status

from app.api.auth_dependencies import require_admin_user
from app.core.settings import get_settings
from app.db.auth_models import User
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import Installation, ProviderConnection
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

LEGACY_SETUP_UNAVAILABLE_DETAIL = "Legacy setup is unavailable for this workspace."


def require_legacy_setup_workspace(
    user: Annotated[
        User,
        Depends(require_admin_user),
    ],
) -> int:
    """Allow legacy setup mutations only for legacy installations.

    SaaS-managed workspaces (non-null ``registration_status``) must use
    the SaaS onboarding flow and may never drive the legacy setup routes.
    """

    workspace_id = user.workspace_id

    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
        )

    with SessionFactory() as session:
        workspace = session.get(
            Installation,
            workspace_id,
        )

    if workspace is None or workspace.registration_status is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=LEGACY_SETUP_UNAVAILABLE_DETAIL,
        )

    return workspace.id


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
    keyring = settings.provider_credential_keyring()

    if not keyring.configured:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    with SessionFactory() as session:
        connection = configure_provider_connection(
            session,
            provider=provider,
            display_name=display_name,
            credentials=credentials,
            credential_keyring=keyring,
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
    keyring = settings.provider_credential_keyring()

    if not keyring.configured:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    with SessionFactory() as session:
        return finalize_installation_setup(
            session,
            credential_keyring=keyring,
        )


def get_setup_finalizer() -> SetupFinalizer:
    """Return the production setup finalizer."""

    return finalize_setup
