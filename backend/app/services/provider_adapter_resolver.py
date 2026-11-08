"""Resolve provider adapters from active provider connections."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.db import model_registry  # noqa: F401
from app.db.models import Season
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)
from app.providers.base import ProviderAdapter
from app.providers.credential_vault import (
    ProviderCredentialConfigurationError,
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.providers.espn import (
    credentials_from_values,
    register_espn_provider,
)
from app.providers.registry import ProviderRegistry


class ProviderAdapterResolutionError(RuntimeError):
    """An active provider adapter could not be resolved safely."""


@dataclass(frozen=True, slots=True)
class ActiveProviderContext:
    """Database context required for one provider synchronization."""

    installation_id: int
    season_id: int
    provider_connection_id: int
    provider: FantasyProvider
    credentials_source: str


def build_provider_registry() -> ProviderRegistry:
    """Build the registry of currently implemented providers."""

    registry = ProviderRegistry()

    register_espn_provider(registry)

    return registry


def resolve_active_provider_context(
    *,
    session: Session,
) -> tuple[
    Installation,
    Season,
    ProviderConnection,
]:
    """Resolve the single installation's active season and connection."""

    installations = session.scalars(select(Installation).order_by(Installation.id)).all()

    active_installations = [
        installation for installation in installations if installation.status.value == "active"
    ]

    if not active_installations:
        raise ProviderAdapterResolutionError("No active installation is configured.")

    if len(active_installations) > 1:
        raise ProviderAdapterResolutionError("Multiple active installations are configured.")

    installation = active_installations[0]

    if installation.active_season_id is None:
        raise ProviderAdapterResolutionError(
            "The active installation does not have an active season."
        )

    season = session.get(
        Season,
        installation.active_season_id,
    )

    if season is None:
        raise ProviderAdapterResolutionError("The active season does not exist.")

    if season.provider_connection_id is None:
        raise ProviderAdapterResolutionError(
            "The active season does not have a provider connection."
        )

    connection = session.get(
        ProviderConnection,
        season.provider_connection_id,
    )

    if connection is None:
        raise ProviderAdapterResolutionError("The active provider connection does not exist.")

    if connection.installation_id != installation.id:
        raise ProviderAdapterResolutionError(
            "The active provider connection belongs to another installation."
        )

    if not connection.is_active:
        raise ProviderAdapterResolutionError("The active season's provider connection is disabled.")

    if season.league.provider is not connection.provider:
        raise ProviderAdapterResolutionError(
            "The active season and provider connection do not use the same provider."
        )

    return (
        installation,
        season,
        connection,
    )


def resolve_connection_credentials(
    *,
    connection: ProviderConnection,
    season: Season,
    settings: Settings,
) -> tuple[
    ProviderCredentials,
    str,
]:
    """Resolve stored credentials or a temporary environment fallback."""

    encrypted = connection.encrypted_credentials

    if encrypted is not None:
        key_setting = settings.provider_credential_key

        if key_setting is None:
            raise ProviderAdapterResolutionError(
                "Stored provider credentials require PROVIDER_CREDENTIAL_KEY."
            )

        try:
            vault = ProviderCredentialVault(
                keys={connection.credential_version: (key_setting.get_secret_value())}
            )

            envelope = vault.decrypt(
                encrypted_credentials=encrypted,
                key_version=(connection.credential_version),
                expected_provider=(connection.provider),
            )
        except ProviderCredentialConfigurationError as exc:
            raise ProviderAdapterResolutionError(
                "Provider credential encryption is not configured correctly."
            ) from exc
        except Exception as exc:
            from app.providers.credential_vault import (
                ProviderCredentialDecryptionError,
            )

            if isinstance(
                exc,
                ProviderCredentialDecryptionError,
            ):
                raise ProviderAdapterResolutionError(
                    "Stored provider credentials could not be decrypted."
                ) from exc

            raise

        return (
            envelope.credentials,
            "stored",
        )

    if connection.provider is FantasyProvider.ESPN:
        espn_s2 = settings.espn_s2
        espn_swid = settings.espn_swid

        if espn_s2 is None or espn_swid is None:
            raise ProviderAdapterResolutionError(
                "The ESPN environment fallback requires both ESPN_S2 and ESPN_SWID."
            )

        return (
            credentials_from_values(
                league_id=(season.league.provider_league_id),
                season=season.provider_season_id,
                espn_s2=(espn_s2.get_secret_value()),
                swid=(espn_swid.get_secret_value()),
            ),
            "environment_fallback",
        )

    raise ProviderAdapterResolutionError(
        "The active provider connection does not have stored credentials."
    )


def resolve_active_provider_adapter(
    *,
    session: Session,
    settings: Settings,
    registry: ProviderRegistry | None = None,
) -> tuple[
    ActiveProviderContext,
    ProviderAdapter,
]:
    """Resolve and construct the active provider adapter dynamically."""

    (
        installation,
        season,
        connection,
    ) = resolve_active_provider_context(session=session)

    credentials, source = resolve_connection_credentials(
        connection=connection,
        season=season,
        settings=settings,
    )

    active_registry = registry if registry is not None else build_provider_registry()

    try:
        adapter = active_registry.create(
            connection.provider,
            credentials,
        )
    except Exception as exc:
        from app.providers.errors import (
            ProviderNotSupportedError,
        )

        if isinstance(
            exc,
            ProviderNotSupportedError,
        ):
            raise ProviderAdapterResolutionError(
                "The active fantasy provider is not implemented."
            ) from exc

        raise

    return (
        ActiveProviderContext(
            installation_id=installation.id,
            season_id=season.id,
            provider_connection_id=(connection.id),
            provider=connection.provider,
            credentials_source=source,
        ),
        adapter,
    )
