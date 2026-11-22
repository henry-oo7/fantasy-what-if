"""Validate and store provider credentials during setup."""

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)
from app.providers.credential_keyring import ProviderCredentialKeyring
from app.providers.credential_vault import (
    CREDENTIAL_SCHEMA_VERSION,
    ProviderCredentialConfigurationError,
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.providers.errors import (
    ProviderNotSupportedError,
)
from app.providers.registry import ProviderRegistry
from app.services.provider_adapter_resolver import (
    build_provider_registry,
)

SETUP_PROVIDER_LOCK_KEY = 1_179_921_929


class SetupProviderConnectionError(RuntimeError):
    """Base error for setup provider persistence."""


class SetupProviderConnectionUnavailableError(SetupProviderConnectionError):
    """Provider setup is unavailable for the lifecycle state."""


class SetupProviderAlreadyConfiguredError(SetupProviderConnectionError):
    """A provider connection already exists."""


class SetupProviderValidationError(SetupProviderConnectionError):
    """The provider rejected the supplied configuration."""


def configure_provider_connection(
    session: Session,
    *,
    provider: FantasyProvider,
    display_name: str,
    credentials: ProviderCredentials,
    credential_keyring: ProviderCredentialKeyring,
    registry: ProviderRegistry | None = None,
    now: datetime | None = None,
) -> ProviderConnection:
    """Validate, encrypt, and persist one provider connection."""

    session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                :lock_key
            )
            """
        ),
        {
            "lock_key": SETUP_PROVIDER_LOCK_KEY,
        },
    )

    installations = session.scalars(
        select(Installation)
        .where(Installation.registration_status.is_(None))
        .order_by(Installation.id)
        .with_for_update()
    ).all()

    if len(installations) != 1:
        raise SetupProviderConnectionUnavailableError(
            "Exactly one installation must exist before configuring a provider."
        )

    installation = installations[0]

    if installation.status is not InstallationStatus.SETUP_IN_PROGRESS:
        raise SetupProviderConnectionUnavailableError(
            "Provider configuration is available only while setup is in progress."
        )

    existing_connection = session.scalar(
        select(ProviderConnection.id)
        .where(ProviderConnection.installation_id == installation.id)
        .limit(1)
    )

    if existing_connection is not None:
        raise SetupProviderAlreadyConfiguredError("A provider connection is already configured.")

    active_registry = registry if registry is not None else build_provider_registry()

    try:
        adapter = active_registry.create(
            provider,
            credentials,
        )
    except ProviderNotSupportedError as exc:
        raise SetupProviderValidationError(
            "The selected fantasy provider is not implemented."
        ) from exc

    validation = adapter.validate_configuration()

    if not validation.is_valid:
        raise SetupProviderValidationError(validation.safe_message)

    if not credential_keyring.configured:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    vault = ProviderCredentialVault(
        keys=credential_keyring.keys,
    )

    encrypted_credentials = vault.encrypt(
        provider=provider,
        credentials=credentials,
        key_version=credential_keyring.current_version,
    )

    normalized_display_name = " ".join(display_name.strip().split())

    if not normalized_display_name:
        raise ValueError("Provider display name cannot be empty.")

    if len(normalized_display_name) > 120:
        raise ValueError("Provider display name cannot exceed 120 characters.")

    connection = ProviderConnection(
        installation_id=installation.id,
        provider=provider,
        display_name=normalized_display_name,
        encrypted_credentials=encrypted_credentials,
        credential_version=(credential_keyring.current_version),
        credential_schema_version=CREDENTIAL_SCHEMA_VERSION,
        is_active=True,
        last_validated_at=now or datetime.now(UTC),
        last_validation_error=None,
    )

    session.add(connection)
    session.flush()

    return connection
