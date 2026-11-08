"""Canonical ESPN provider adapter."""

from __future__ import annotations

from collections.abc import Callable

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
    ProviderMatchup,
    ProviderScheduleRules,
    ProviderValidationResult,
    ProviderWeeklyRoster,
    immutable_mapping,
)
from app.providers.errors import (
    ProviderError,
    ProviderErrorContext,
    ProviderNotFoundError,
    ProviderResponseError,
)
from app.providers.espn.adapter import (
    create_matchups_with_byes,
    create_provider_team,
    create_provider_weekly_roster,
)
from app.providers.espn.client import (
    create_espn_client,
    parse_espn_configuration,
)
from app.providers.espn.protocols import EspnLeague
from app.providers.registry import ProviderRegistry

EspnClientFactory = Callable[
    [ProviderCredentials],
    EspnLeague,
]


def default_espn_client_factory(
    credentials: ProviderCredentials,
) -> EspnLeague:
    """Construct the default espn-api client."""

    return create_espn_client(credentials)


class EspnProvider:
    """Read-only canonical adapter for ESPN fantasy football."""

    provider = FantasyProvider.ESPN

    def __init__(
        self,
        credentials: ProviderCredentials,
        *,
        client_factory: EspnClientFactory = (default_espn_client_factory),
    ) -> None:
        self._credentials = credentials
        self._configuration = parse_espn_configuration(credentials)
        self._client_factory = client_factory
        self._client: EspnLeague | None = None

    def _error_context(
        self,
        operation: str,
        *,
        retryable: bool = False,
    ) -> ProviderErrorContext:
        return ProviderErrorContext(
            provider=self.provider,
            operation=operation,
            retryable=retryable,
        )

    def _get_client(
        self,
        *,
        operation: str,
    ) -> EspnLeague:
        if self._client is not None:
            return self._client

        try:
            client = self._client_factory(self._credentials)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderResponseError(
                context=self._error_context(
                    operation,
                    retryable=False,
                ),
                safe_message=("ESPN could not load the configured league."),
            ) from exc

        self._client = client

        return client

    def _validate_requested_identity(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        operation: str,
    ) -> None:
        if (
            provider_league_id != self._configuration.provider_league_id
            or provider_season_id != self._configuration.provider_season_id
        ):
            raise ProviderNotFoundError(
                context=self._error_context(operation),
                safe_message=(
                    "The requested ESPN league or season does not match the configured connection."
                ),
            )

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        """Validate configuration by loading the ESPN league."""

        try:
            client = self._get_client(operation="validate_configuration")
            self._validate_client_identity(
                client,
                operation="validate_configuration",
            )
        except ProviderError as exc:
            return ProviderValidationResult(
                is_valid=False,
                provider=self.provider,
                safe_message=exc.safe_message,
                metadata=immutable_mapping(
                    {
                        "operation": exc.operation,
                        "retryable": exc.retryable,
                    }
                ),
            )

        return ProviderValidationResult(
            is_valid=True,
            provider=self.provider,
            safe_message=("ESPN configuration is valid."),
            metadata=immutable_mapping(
                {
                    "private_league": (self._configuration.is_private_league_configuration),
                }
            ),
        )

    def _validate_client_identity(
        self,
        client: EspnLeague,
        *,
        operation: str,
    ) -> None:
        try:
            league_id = str(client.league_id)
            season_id = str(client.year)
        except Exception as exc:
            raise ProviderResponseError(
                context=self._error_context(operation),
                safe_message=("ESPN returned invalid league identity data."),
            ) from exc

        if (
            league_id != self._configuration.provider_league_id
            or season_id != self._configuration.provider_season_id
        ):
            raise ProviderResponseError(
                context=self._error_context(operation),
                safe_message=(
                    "ESPN returned a different league or season than the configured connection."
                ),
            )

    def discover_leagues(
        self,
    ) -> tuple[ProviderLeagueReference, ...]:
        """Return the explicitly configured ESPN league."""

        client = self._get_client(operation="discover_leagues")
        self._validate_client_identity(
            client,
            operation="discover_leagues",
        )

        try:
            name = str(client.settings.name).strip()
        except Exception as exc:
            raise ProviderResponseError(
                context=self._error_context("discover_leagues"),
                safe_message=("ESPN returned invalid league settings."),
            ) from exc

        return (
            ProviderLeagueReference(
                provider_league_id=(self._configuration.provider_league_id),
                provider_season_id=(self._configuration.provider_season_id),
                name=name,
                sport=FantasySport.FOOTBALL,
                season_year=self._configuration.season,
                logo_url=None,
            ),
        )

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> ProviderLeague:
        """Return canonical ESPN league configuration."""

        operation = "fetch_league"
        self._validate_requested_identity(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
            operation=operation,
        )

        client = self._get_client(operation=operation)
        self._validate_client_identity(
            client,
            operation=operation,
        )

        try:
            current_week = int(client.current_week)
            regular_season_weeks = int(client.settings.reg_season_count)
            name = str(client.settings.name).strip()
            teams = tuple(
                sorted(
                    (create_provider_team(team) for team in client.teams),
                    key=lambda team: team.provider_team_id,
                )
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderResponseError(
                context=self._error_context(operation),
                safe_message=("ESPN returned invalid league data."),
            ) from exc

        schedule_rules = ProviderScheduleRules(
            regular_season_weeks=(regular_season_weeks),
            playoff_week_start=None,
            playoff_week_end=None,
            has_divisions=False,
            has_median_game=False,
            has_doubleheaders=False,
            allows_ties=True,
            metadata=immutable_mapping(
                {
                    "provider": "espn",
                    "configuration_complete": False,
                }
            ),
        )

        capabilities = ProviderCapabilities(
            supports_head_to_head=True,
            supports_weekly_matchups=True,
            supports_lineup_history=True,
            supports_optimal_lineups=False,
            supports_schedule_swap=True,
            supports_all_play=True,
            supports_league_average=True,
            supports_team_logos=any(team.logo_url is not None for team in teams),
        )

        return ProviderLeague(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
            name=name,
            sport=FantasySport.FOOTBALL,
            season_year=self._configuration.season,
            current_week=current_week,
            regular_season_weeks=(regular_season_weeks),
            playoff_week_start=None,
            playoff_week_end=None,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=teams,
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=schedule_rules,
            capabilities=capabilities,
            metadata=immutable_mapping(
                {
                    "provider": "espn",
                    "experimental_connector": True,
                    "rules_imported": False,
                }
            ),
        )

    def fetch_matchups(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderMatchup, ...]:
        """Return canonical ESPN matchups for requested weeks."""

        operation = "fetch_matchups"
        self._validate_requested_identity(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
            operation=operation,
        )

        normalized_weeks = self._normalize_weeks(
            weeks,
            operation=operation,
        )
        client = self._get_client(operation=operation)
        self._validate_client_identity(
            client,
            operation=operation,
        )

        provider_team_ids = tuple(str(team.team_id) for team in client.teams)
        current_week = int(client.current_week)
        results: list[ProviderMatchup] = []

        for week in normalized_weeks:
            try:
                raw_matchups = tuple(client.box_scores(week=week))
            except Exception as exc:
                raise ProviderResponseError(
                    context=self._error_context(
                        operation,
                        retryable=True,
                    ),
                    safe_message=(f"ESPN could not load matchup week {week}."),
                ) from exc

            is_complete = week < current_week

            if is_complete and not raw_matchups:
                raise ProviderResponseError(
                    context=self._error_context(operation),
                    safe_message=(f"ESPN returned no matchups for completed week {week}."),
                )

            try:
                results.extend(
                    create_matchups_with_byes(
                        week=week,
                        matchups=raw_matchups,
                        provider_team_ids=(provider_team_ids),
                        is_complete=is_complete,
                    )
                )
            except ValueError as exc:
                raise ProviderResponseError(
                    context=self._error_context(operation),
                    safe_message=(f"ESPN returned inconsistent matchup data for week {week}."),
                ) from exc

        return tuple(
            sorted(
                results,
                key=lambda matchup: (
                    matchup.week,
                    matchup.home_provider_team_id,
                    (matchup.away_provider_team_id or ""),
                ),
            )
        )

    def fetch_weekly_rosters(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderWeeklyRoster, ...]:
        """Return canonical ESPN team rosters for requested weeks."""

        operation = "fetch_weekly_rosters"
        self._validate_requested_identity(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
            operation=operation,
        )

        normalized_weeks = self._normalize_weeks(
            weeks,
            operation=operation,
        )
        client = self._get_client(operation=operation)
        self._validate_client_identity(
            client,
            operation=operation,
        )

        results: list[ProviderWeeklyRoster] = []
        seen_team_weeks: set[tuple[int, str]] = set()

        for week in normalized_weeks:
            try:
                raw_matchups = tuple(client.box_scores(week=week))
            except Exception as exc:
                raise ProviderResponseError(
                    context=self._error_context(
                        operation,
                        retryable=True,
                    ),
                    safe_message=(f"ESPN could not load roster week {week}."),
                ) from exc

            if week < int(client.current_week) and not raw_matchups:
                raise ProviderResponseError(
                    context=self._error_context(operation),
                    safe_message=(f"ESPN returned no rosters for completed week {week}."),
                )

            for matchup in raw_matchups:
                lineup_groups = (
                    (
                        str(matchup.home_team.team_id),
                        matchup.home_lineup,
                    ),
                    (
                        str(matchup.away_team.team_id),
                        matchup.away_lineup,
                    ),
                )

                for provider_team_id, lineup in lineup_groups:
                    team_week = (
                        week,
                        provider_team_id,
                    )

                    if team_week in seen_team_weeks:
                        raise ProviderResponseError(
                            context=self._error_context(operation),
                            safe_message=(
                                "ESPN returned duplicate roster data for one team and week."
                            ),
                        )

                    seen_team_weeks.add(team_week)

                    try:
                        results.append(
                            create_provider_weekly_roster(
                                provider_team_id=(provider_team_id),
                                week=week,
                                lineup=lineup,
                            )
                        )
                    except ValueError as exc:
                        raise ProviderResponseError(
                            context=self._error_context(operation),
                            safe_message=(
                                f"ESPN returned inconsistent roster data for week {week}."
                            ),
                        ) from exc

        return tuple(
            sorted(
                results,
                key=lambda roster: (
                    roster.week,
                    roster.provider_team_id,
                ),
            )
        )

    def _normalize_weeks(
        self,
        weeks: tuple[int, ...],
        *,
        operation: str,
    ) -> tuple[int, ...]:
        normalized_weeks = tuple(sorted(set(weeks)))

        if any(not isinstance(week, int) or week < 1 for week in normalized_weeks):
            raise ProviderResponseError(
                context=self._error_context(operation),
                safe_message=("Requested ESPN weeks must be positive integers."),
            )

        return normalized_weeks


def create_espn_provider(
    credentials: ProviderCredentials,
) -> ProviderAdapter:
    """Construct the canonical ESPN provider."""

    return EspnProvider(credentials)


def register_espn_provider(
    registry: ProviderRegistry,
    *,
    replace: bool = False,
) -> None:
    """Register ESPN without importing or constructing it eagerly."""

    registry.register(
        FantasyProvider.ESPN,
        create_espn_provider,
        replace=replace,
    )
