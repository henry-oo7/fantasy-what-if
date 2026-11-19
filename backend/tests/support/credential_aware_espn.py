"""Credential-aware synthetic ESPN adapters for integration tests."""

from __future__ import annotations

from decimal import Decimal

from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
)
from app.providers.domain import (
    ProviderCapabilities,
    ProviderCredentials,
    ProviderLeague,
    ProviderMatchup,
    ProviderScheduleRules,
    ProviderTeam,
    ProviderValidationResult,
    ProviderWeeklyRoster,
)
from app.providers.registry import ProviderRegistry
from tests.support.multi_workspace_espn import (
    FIRST_COOKIE_MARKER,
    FIRST_PROVIDER_LEAGUE_ID,
    FIRST_SWID_MARKER,
    SECOND_COOKIE_MARKER,
    SECOND_PROVIDER_LEAGUE_ID,
    SECOND_SWID_MARKER,
)


class CredentialAwareEspnAdapter:
    """Return one deterministic dataset selected by credentials."""

    provider = FantasyProvider.ESPN

    def __init__(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        cookie_marker: str,
        swid_marker: str,
        dataset_marker: str,
    ) -> None:
        self.provider_league_id = provider_league_id
        self.provider_season_id = provider_season_id
        self.cookie_marker = cookie_marker
        self.swid_marker = swid_marker
        self.dataset_marker = dataset_marker
        self.requested_league_ids: list[str] = []

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        return ProviderValidationResult(
            is_valid=True,
            provider=self.provider,
            safe_message=("Synthetic ESPN configuration is valid."),
        )

    def discover_leagues(self) -> tuple:
        return ()

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> ProviderLeague:
        self._validate_request(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
        )
        self.requested_league_ids.append(provider_league_id)

        return ProviderLeague(
            provider_league_id=(self.provider_league_id),
            provider_season_id=(self.provider_season_id),
            name=(f"Synthetic ESPN {self.dataset_marker.upper()} League"),
            sport=FantasySport.FOOTBALL,
            season_year=2026,
            current_week=1,
            regular_season_weeks=2,
            playoff_week_start=None,
            playoff_week_end=None,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=(
                self._team(
                    provider_team_id=(f"{self.dataset_marker}-team-1"),
                    ordinal=1,
                ),
                self._team(
                    provider_team_id=(f"{self.dataset_marker}-team-2"),
                    ordinal=2,
                ),
            ),
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=ProviderScheduleRules(
                regular_season_weeks=2,
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
    ) -> tuple[ProviderMatchup, ...]:
        self._validate_request(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
        )

        return tuple(
            ProviderMatchup(
                week=week,
                home_provider_team_id=(f"{self.dataset_marker}-team-1"),
                away_provider_team_id=(f"{self.dataset_marker}-team-2"),
                home_score=None,
                away_score=None,
                is_complete=False,
                is_bye=False,
                metadata={
                    "dataset": self.dataset_marker,
                },
            )
            for week in weeks
        )

    def fetch_weekly_rosters(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderWeeklyRoster, ...]:
        self._validate_request(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
        )

        assert weeks == ()

        return ()

    def _team(
        self,
        *,
        provider_team_id: str,
        ordinal: int,
    ) -> ProviderTeam:
        return ProviderTeam(
            provider_team_id=provider_team_id,
            name=(f"{self.dataset_marker.upper()} Team {ordinal}"),
            abbreviation=(f"{self.dataset_marker[:1].upper()}{ordinal}"),
            owner_display_name=(f"{self.dataset_marker.upper()} Manager {ordinal}"),
            wins=ordinal,
            losses=0,
            ties=0,
            points_for=Decimal(f"{1000 + ordinal}.00"),
            points_against=Decimal(f"{900 + ordinal}.00"),
            metadata={
                "dataset": self.dataset_marker,
            },
        )

    def _validate_request(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> None:
        assert provider_league_id == (self.provider_league_id)
        assert provider_season_id == (self.provider_season_id)


class CredentialAwareEspnFactory:
    """Build adapters from decrypted synthetic credentials."""

    def __init__(self) -> None:
        self.adapters: list[CredentialAwareEspnAdapter] = []
        self.received_credentials: list[dict[str, str]] = []

    def __call__(
        self,
        credentials: ProviderCredentials,
    ) -> CredentialAwareEspnAdapter:
        values = dict(credentials.values)
        self.received_credentials.append(values)

        league_id = values.get("league_id")
        season = values.get("season")
        cookie = values.get("espn_s2")
        swid = values.get("swid")

        if (
            league_id == FIRST_PROVIDER_LEAGUE_ID
            and cookie == FIRST_COOKIE_MARKER
            and swid == FIRST_SWID_MARKER
        ):
            marker = "alpha"
        elif (
            league_id == SECOND_PROVIDER_LEAGUE_ID
            and cookie == SECOND_COOKIE_MARKER
            and swid == SECOND_SWID_MARKER
        ):
            marker = "beta"
        else:
            raise AssertionError(
                "The fake ESPN factory received credentials from an unexpected workspace."
            )

        if season != "2026":
            raise AssertionError("The fake ESPN factory received an unexpected season.")

        adapter = CredentialAwareEspnAdapter(
            provider_league_id=league_id,
            provider_season_id=season,
            cookie_marker=cookie,
            swid_marker=swid,
            dataset_marker=marker,
        )
        self.adapters.append(adapter)

        return adapter


def credential_aware_espn_registry() -> tuple[
    ProviderRegistry,
    CredentialAwareEspnFactory,
]:
    """Build a registry and observable synthetic ESPN factory."""

    factory = CredentialAwareEspnFactory()
    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.ESPN,
        factory,
    )

    return registry, factory
