"""Provider capabilities exposed during installation setup."""

from dataclasses import dataclass

from app.db.platform_enums import FantasyProvider
from app.services.provider_adapter_resolver import (
    build_provider_registry,
)


@dataclass(frozen=True, slots=True)
class SetupProvider:
    """Safe provider metadata for the setup interface."""

    provider: FantasyProvider
    display_name: str
    implemented: bool
    requires_credentials: bool
    credential_fields: tuple[str, ...]


_PROVIDER_METADATA = {
    FantasyProvider.ESPN: SetupProvider(
        provider=FantasyProvider.ESPN,
        display_name="ESPN Fantasy Football",
        implemented=False,
        requires_credentials=True,
        credential_fields=(
            "league_id",
            "season",
            "espn_s2",
            "swid",
        ),
    ),
    FantasyProvider.SLEEPER: SetupProvider(
        provider=FantasyProvider.SLEEPER,
        display_name="Sleeper",
        implemented=False,
        requires_credentials=False,
        credential_fields=(),
    ),
    FantasyProvider.YAHOO: SetupProvider(
        provider=FantasyProvider.YAHOO,
        display_name="Yahoo Fantasy Sports",
        implemented=False,
        requires_credentials=True,
        credential_fields=(),
    ),
}


def list_setup_providers() -> tuple[SetupProvider, ...]:
    """Return stable setup metadata for known providers."""

    registry = build_provider_registry()
    implemented = set(registry.registered_providers())

    return tuple(
        SetupProvider(
            provider=metadata.provider,
            display_name=metadata.display_name,
            implemented=(metadata.provider in implemented),
            requires_credentials=(metadata.requires_credentials),
            credential_fields=(metadata.credential_fields),
        )
        for metadata in _PROVIDER_METADATA.values()
    )
