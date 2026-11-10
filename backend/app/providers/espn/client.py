"""Construct ESPN API clients from canonical provider credentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from espn_api.football import League

from app.db.platform_enums import FantasyProvider
from app.providers.domain import ProviderCredentials
from app.providers.errors import (
    ProviderConfigurationError,
    ProviderErrorContext,
)


class EspnLeagueFactory(Protocol):
    """Callable compatible with the espn-api League constructor."""

    def __call__(
        self,
        *,
        league_id: int,
        year: int,
        espn_s2: str | None,
        swid: str | None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class EspnConfiguration:
    """Validated configuration required to construct ESPN clients."""

    league_id: int
    season: int
    espn_s2: str | None
    swid: str | None

    @property
    def provider_league_id(self) -> str:
        return str(self.league_id)

    @property
    def provider_season_id(self) -> str:
        return str(self.season)

    @property
    def is_private_league_configuration(self) -> bool:
        return self.espn_s2 is not None and self.swid is not None


def normalize_optional_credential(
    value: str | None,
) -> str | None:
    """Normalize an optional credential without logging its value."""

    if value is None:
        return None

    normalized = value.strip()

    return normalized or None


def require_positive_integer(
    values: dict[str, str],
    key: str,
) -> int:
    """Read one required positive integer credential."""

    raw_value = normalize_optional_credential(values.get(key))

    if raw_value is None:
        raise ProviderConfigurationError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="parse_configuration",
            ),
            safe_message=(f"ESPN configuration requires {key}."),
        )

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ProviderConfigurationError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="parse_configuration",
            ),
            safe_message=(f"ESPN configuration value {key} must be a positive integer."),
        ) from exc

    if parsed < 1:
        raise ProviderConfigurationError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="parse_configuration",
            ),
            safe_message=(f"ESPN configuration value {key} must be a positive integer."),
        )

    return parsed


def parse_espn_configuration(
    credentials: ProviderCredentials,
) -> EspnConfiguration:
    """Convert canonical credentials into validated ESPN settings."""

    values = dict(credentials.values)

    league_id = require_positive_integer(
        values,
        "league_id",
    )
    season = require_positive_integer(
        values,
        "season",
    )

    espn_s2 = normalize_optional_credential(values.get("espn_s2"))
    swid = normalize_optional_credential(values.get("swid"))

    if (espn_s2 is None) != (swid is None):
        raise ProviderConfigurationError(
            context=ProviderErrorContext(
                provider=FantasyProvider.ESPN,
                operation="parse_configuration",
            ),
            safe_message=("Private ESPN leagues require both espn_s2 and swid credentials."),
        )

    return EspnConfiguration(
        league_id=league_id,
        season=season,
        espn_s2=espn_s2,
        swid=swid,
    )


def create_espn_client(
    credentials: ProviderCredentials,
    *,
    league_factory: EspnLeagueFactory = League,
) -> Any:
    """Construct an espn-api league without exposing credentials."""

    configuration = parse_espn_configuration(credentials)

    return league_factory(
        league_id=configuration.league_id,
        year=configuration.season,
        espn_s2=configuration.espn_s2,
        swid=configuration.swid,
    )


def credentials_from_values(
    *,
    league_id: int | str,
    season: int | str,
    espn_s2: str | None = None,
    swid: str | None = None,
) -> ProviderCredentials:
    """Create canonical ESPN credentials from trusted settings values."""

    values = {
        "league_id": str(league_id),
        "season": str(season),
    }

    normalized_espn_s2 = normalize_optional_credential(espn_s2)
    normalized_swid = normalize_optional_credential(swid)

    if normalized_espn_s2 is not None:
        values["espn_s2"] = normalized_espn_s2

    if normalized_swid is not None:
        values["swid"] = normalized_swid

    return ProviderCredentials(values=values)
