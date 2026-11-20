"""Safely replace one workspace's stored provider credentials."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import League, Season
from app.db.platform_models import (
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
from app.providers.credential_vault import (
    CREDENTIAL_SCHEMA_VERSION,
    ProviderCredentialConfigurationError,
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.providers.errors import (
    ProviderError,
    ProviderNotSupportedError,
)
from app.providers.registry import ProviderRegistry
from app.repositories.provider_league_claim_repository import (
    normalize_provider_league_id,
)
from app.services.provider_adapter_resolver import (
    build_provider_registry,
)


class ProviderCredentialReplacementError(RuntimeError):
    """Base error for provider credential replacement."""


class ProviderCredentialReplacementUnavailableError(ProviderCredentialReplacementError):
    """The workspace does not support credential replacement."""


class ProviderCredentialReplacementValidationError(ProviderCredentialReplacementError):
    """Replacement credentials failed safe validation."""


@dataclass(frozen=True, slots=True)
class ProviderCredentialReplacementResult:
    """Safe metadata returned after credential replacement."""

    workspace_id: int
    connection_id: int
    provider: str
    provider_league_id: str
    provider_season_id: str
    credential_version: int
    credential_schema_version: int
    validated_at: datetime


def replace_provider_credentials(
    session: Session,
    *,
    workspace_id: int,
    submitted_credentials: ProviderCredentials,
    credential_keyring: ProviderCredentialKeyring,
    registry: ProviderRegistry | None = None,
    now: datetime | None = None,
) -> ProviderCredentialReplacementResult:
    """Validate and atomically replace one active provider envelope."""

    installation = session.get(
        Installation,
        workspace_id,
        with_for_update=True,
    )

    if installation is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The requested workspace does not exist."
        )

    if installation.active_league_id is None or installation.active_season_id is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The workspace does not have an active provider season."
        )

    league = session.get(
        League,
        installation.active_league_id,
    )
    season = session.get(
        Season,
        installation.active_season_id,
    )

    if league is None or season is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The active provider context is unavailable."
        )

    if league.installation_id != installation.id or season.league_id != league.id:
        raise ProviderCredentialReplacementUnavailableError(
            "The active provider context belongs to another workspace."
        )

    if (
        league.provider is None
        or league.provider_league_id is None
        or season.provider_season_id is None
        or season.provider_connection_id is None
    ):
        raise ProviderCredentialReplacementUnavailableError(
            "The active provider context is incomplete."
        )

    connection = session.scalar(
        select(ProviderConnection)
        .where(
            ProviderConnection.id == season.provider_connection_id,
            ProviderConnection.installation_id == installation.id,
            ProviderConnection.provider == league.provider,
            ProviderConnection.is_active.is_(True),
        )
        .with_for_update()
    )

    if connection is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The active provider connection is unavailable."
        )

    claim = session.scalar(
        select(ProviderLeagueClaim)
        .where(
            ProviderLeagueClaim.workspace_id == installation.id,
            ProviderLeagueClaim.league_id == league.id,
            ProviderLeagueClaim.provider == connection.provider,
        )
        .with_for_update()
    )

    if claim is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The workspace provider claim is unavailable."
        )

    claimed_identity = normalize_provider_league_id(claim.provider_league_id)
    active_identity = normalize_provider_league_id(league.provider_league_id)

    if (
        claim.normalized_provider_league_id != claimed_identity
        or claimed_identity != active_identity
    ):
        raise ProviderCredentialReplacementUnavailableError(
            "The workspace provider identity is inconsistent."
        )

    replacement_values = dict(submitted_credentials.values)
    replacement_values["league_id"] = league.provider_league_id
    replacement_values["season"] = season.provider_season_id

    replacement_credentials = ProviderCredentials(values=replacement_values)

    active_registry = registry if registry is not None else build_provider_registry()

    try:
        provider = active_registry.create(
            connection.provider,
            replacement_credentials,
        )
    except ProviderNotSupportedError as exc:
        raise ProviderCredentialReplacementUnavailableError(
            "The configured fantasy provider is not implemented."
        ) from exc

    try:
        validation = provider.validate_configuration()

        if not validation.is_valid:
            raise ProviderCredentialReplacementValidationError(validation.safe_message)

        provider_league = provider.fetch_league(
            provider_league_id=claim.provider_league_id,
            provider_season_id=season.provider_season_id,
        )
    except ProviderCredentialReplacementValidationError:
        raise
    except ProviderError as exc:
        raise ProviderCredentialReplacementValidationError(exc.safe_message) from exc

    returned_identity = normalize_provider_league_id(provider_league.provider_league_id)

    if returned_identity != claimed_identity:
        raise ProviderCredentialReplacementValidationError(
            "The replacement credentials do not access the claimed league."
        )

    if provider_league.provider_season_id.strip() != season.provider_season_id.strip():
        raise ProviderCredentialReplacementValidationError(
            "The replacement credentials do not access the active season."
        )

    if not credential_keyring.configured:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    vault = ProviderCredentialVault(
        keys=credential_keyring.keys,
    )
    encrypted_credentials = vault.encrypt(
        provider=connection.provider,
        credentials=replacement_credentials,
        key_version=credential_keyring.current_version,
    )

    timestamp = now or datetime.now(UTC)

    connection.encrypted_credentials = encrypted_credentials
    connection.credential_version = credential_keyring.current_version
    connection.credential_schema_version = CREDENTIAL_SCHEMA_VERSION
    connection.last_validated_at = timestamp
    connection.last_validation_error = None

    session.flush()

    return ProviderCredentialReplacementResult(
        workspace_id=installation.id,
        connection_id=connection.id,
        provider=connection.provider.value,
        provider_league_id=claim.provider_league_id,
        provider_season_id=season.provider_season_id,
        credential_version=connection.credential_version,
        credential_schema_version=(connection.credential_schema_version),
        validated_at=timestamp,
    )
