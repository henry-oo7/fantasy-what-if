"""Lazy registry for fantasy provider adapter factories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.db.platform_enums import FantasyProvider
from app.providers.base import ProviderAdapter
from app.providers.domain import ProviderCredentials
from app.providers.errors import (
    ProviderErrorContext,
    ProviderNotSupportedError,
)

ProviderFactory = Callable[
    [ProviderCredentials],
    ProviderAdapter,
]


@dataclass(slots=True)
class ProviderRegistry:
    """Map provider names to lazily invoked adapter factories."""

    _factories: dict[
        FantasyProvider,
        ProviderFactory,
    ] = field(default_factory=dict)

    def register(
        self,
        provider: FantasyProvider,
        factory: ProviderFactory,
        *,
        replace: bool = False,
    ) -> None:
        """Register one provider factory."""

        if provider in self._factories and not replace:
            raise ValueError(f"Provider {provider.value!r} is already registered.")

        self._factories[provider] = factory

    def unregister(
        self,
        provider: FantasyProvider,
    ) -> None:
        """Remove one registered provider factory."""

        self._factories.pop(provider, None)

    def is_registered(
        self,
        provider: FantasyProvider,
    ) -> bool:
        """Return whether a provider factory is registered."""

        return provider in self._factories

    def registered_providers(
        self,
    ) -> tuple[FantasyProvider, ...]:
        """Return registered providers in stable name order."""

        return tuple(
            sorted(
                self._factories,
                key=lambda provider: provider.value,
            )
        )

    def create(
        self,
        provider: FantasyProvider,
        credentials: ProviderCredentials,
    ) -> ProviderAdapter:
        """Construct an adapter using the registered factory."""

        factory = self._factories.get(provider)

        if factory is None:
            raise ProviderNotSupportedError(
                context=ProviderErrorContext(
                    provider=provider,
                    operation="create_provider",
                    retryable=False,
                )
            )

        adapter = factory(credentials)

        if adapter.provider is not provider:
            raise TypeError("Provider factory returned an adapter for a different provider.")

        if not isinstance(adapter, ProviderAdapter):
            raise TypeError(
                "Provider factory returned an object that does not satisfy ProviderAdapter."
            )

        return adapter


provider_registry = ProviderRegistry()
