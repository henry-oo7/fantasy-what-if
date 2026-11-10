from decimal import Decimal

import pytest

from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
)
from app.providers.base import ProviderAdapter
from app.providers.domain import (
    ProviderCapabilities,
    ProviderCredentials,
    ProviderLeague,
    ProviderLeagueReference,
    ProviderScheduleRules,
    ProviderValidationResult,
)
from app.providers.errors import (
    ProviderNotSupportedError,
)
from app.providers.registry import ProviderRegistry


class FakeProvider:
    provider = FantasyProvider.ESPN

    def __init__(
        self,
        credentials: ProviderCredentials,
    ) -> None:
        self.credentials = credentials

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        return ProviderValidationResult(
            is_valid=True,
            provider=self.provider,
            safe_message="Configuration is valid.",
        )

    def discover_leagues(
        self,
    ) -> tuple[ProviderLeagueReference, ...]:
        return (
            ProviderLeagueReference(
                provider_league_id="529250970",
                provider_season_id="2026",
                name="Jimmy and Friends",
                sport=FantasySport.FOOTBALL,
                season_year=2026,
            ),
        )

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> ProviderLeague:
        return ProviderLeague(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
            name="Jimmy and Friends",
            sport=FantasySport.FOOTBALL,
            season_year=int(provider_season_id),
            current_week=9,
            regular_season_weeks=12,
            playoff_week_start=None,
            playoff_week_end=None,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=(),
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=ProviderScheduleRules(
                regular_season_weeks=12,
                playoff_week_start=None,
                playoff_week_end=None,
                has_divisions=False,
                has_median_game=False,
                has_doubleheaders=False,
                allows_ties=True,
            ),
            capabilities=ProviderCapabilities(
                supports_head_to_head=True,
                supports_weekly_matchups=True,
                supports_lineup_history=False,
                supports_optimal_lineups=False,
                supports_schedule_swap=True,
                supports_all_play=True,
                supports_league_average=True,
                supports_team_logos=False,
            ),
        )

    def fetch_matchups(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple:
        del provider_league_id
        del provider_season_id
        del weeks

        return ()

    def fetch_weekly_rosters(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple:
        del provider_league_id
        del provider_season_id
        del weeks

        return ()


class WrongProvider(FakeProvider):
    provider = FantasyProvider.SLEEPER


class IncompleteProvider:
    provider = FantasyProvider.ESPN


def make_credentials() -> ProviderCredentials:
    return ProviderCredentials(
        values={
            "league_id": "529250970",
        }
    )


def test_fake_provider_satisfies_runtime_protocol() -> None:
    provider = FakeProvider(make_credentials())

    assert isinstance(provider, ProviderAdapter)


def test_registry_constructs_registered_provider() -> None:
    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.ESPN,
        FakeProvider,
    )

    provider = registry.create(
        FantasyProvider.ESPN,
        make_credentials(),
    )

    assert isinstance(provider, FakeProvider)
    assert provider.credentials.values["league_id"] == "529250970"


def test_registry_rejects_duplicate_registration() -> None:
    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.ESPN,
        FakeProvider,
    )

    with pytest.raises(ValueError):
        registry.register(
            FantasyProvider.ESPN,
            FakeProvider,
        )


def test_registry_can_replace_registration() -> None:
    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.ESPN,
        FakeProvider,
    )
    registry.register(
        FantasyProvider.ESPN,
        FakeProvider,
        replace=True,
    )

    assert registry.is_registered(FantasyProvider.ESPN)


def test_registry_returns_stable_provider_order() -> None:
    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.YAHOO,
        FakeProvider,
    )
    registry.register(
        FantasyProvider.ESPN,
        FakeProvider,
    )

    assert registry.registered_providers() == (
        FantasyProvider.ESPN,
        FantasyProvider.YAHOO,
    )


def test_registry_rejects_unregistered_provider() -> None:
    registry = ProviderRegistry()

    with pytest.raises(ProviderNotSupportedError):
        registry.create(
            FantasyProvider.SLEEPER,
            make_credentials(),
        )


def test_registry_rejects_wrong_provider_factory() -> None:
    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.ESPN,
        WrongProvider,
    )

    with pytest.raises(TypeError):
        registry.create(
            FantasyProvider.ESPN,
            make_credentials(),
        )


def test_registry_rejects_incomplete_adapter() -> None:
    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.ESPN,
        lambda credentials: IncompleteProvider(),
    )

    with pytest.raises(TypeError):
        registry.create(
            FantasyProvider.ESPN,
            make_credentials(),
        )


def test_unregister_is_idempotent() -> None:
    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.ESPN,
        FakeProvider,
    )

    registry.unregister(FantasyProvider.ESPN)
    registry.unregister(FantasyProvider.ESPN)

    assert not registry.is_registered(FantasyProvider.ESPN)


def test_fake_league_uses_canonical_values() -> None:
    provider = FakeProvider(make_credentials())

    league = provider.fetch_league(
        provider_league_id="529250970",
        provider_season_id="2026",
    )

    assert league.provider_league_id == "529250970"
    assert league.provider_season_id == "2026"
    assert league.season_year == 2026
    assert isinstance(
        Decimal("0.00"),
        Decimal,
    )
