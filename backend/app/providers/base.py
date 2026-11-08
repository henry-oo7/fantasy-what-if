"""Provider-independent adapter protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.db.platform_enums import FantasyProvider
from app.providers.domain import (
    ProviderLeague,
    ProviderLeagueReference,
    ProviderMatchup,
    ProviderValidationResult,
    ProviderWeeklyRoster,
)


@runtime_checkable
class ProviderAdapter(Protocol):
    """Contract implemented by every fantasy provider."""

    provider: FantasyProvider

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        """Validate credentials and provider connectivity."""
        ...

    def discover_leagues(
        self,
    ) -> tuple[ProviderLeagueReference, ...]:
        """Return leagues visible to the provider identity."""
        ...

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> ProviderLeague:
        """Return canonical league and season configuration."""
        ...

    def fetch_matchups(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderMatchup, ...]:
        """Return canonical matchups for requested weeks."""
        ...

    def fetch_weekly_rosters(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderWeeklyRoster, ...]:
        """Return canonical team rosters for requested weeks."""
        ...
