from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from app.db.platform_enums import FantasyProvider
from app.providers.domain import ProviderCredentials
from app.providers.errors import (
    ProviderConfigurationError,
)
from app.providers.espn.client import (
    EspnConfiguration,
    create_espn_client,
    credentials_from_values,
    parse_espn_configuration,
)


class FakeLeague:
    def __init__(
        self,
        *,
        league_id: int,
        year: int,
        espn_s2: str | None,
        swid: str | None,
    ) -> None:
        self.league_id = league_id
        self.year = year
        self.espn_s2 = espn_s2
        self.swid = swid


def test_public_configuration_parses() -> None:
    configuration = parse_espn_configuration(
        ProviderCredentials(
            values={
                "league_id": "529250970",
                "season": "2026",
            }
        )
    )

    assert configuration.league_id == 529250970
    assert configuration.season == 2026
    assert configuration.provider_league_id == "529250970"
    assert configuration.provider_season_id == "2026"
    assert configuration.espn_s2 is None
    assert configuration.swid is None
    assert configuration.is_private_league_configuration is False


def test_private_configuration_parses() -> None:
    configuration = parse_espn_configuration(
        ProviderCredentials(
            values={
                "league_id": "529250970",
                "season": "2026",
                "espn_s2": " private-cookie ",
                "swid": " {private-swid} ",
            }
        )
    )

    assert configuration.espn_s2 == "private-cookie"
    assert configuration.swid == "{private-swid}"
    assert configuration.is_private_league_configuration is True


@pytest.mark.parametrize(
    "values",
    [
        {"season": "2026"},
        {"league_id": "529250970"},
        {
            "league_id": "not-a-number",
            "season": "2026",
        },
        {
            "league_id": "529250970",
            "season": "not-a-number",
        },
        {
            "league_id": "0",
            "season": "2026",
        },
        {
            "league_id": "529250970",
            "season": "-1",
        },
    ],
)
def test_invalid_required_configuration_is_rejected(
    values: dict[str, str],
) -> None:
    with pytest.raises(ProviderConfigurationError) as error:
        parse_espn_configuration(ProviderCredentials(values=values))

    assert error.value.provider is FantasyProvider.ESPN
    assert error.value.operation == "parse_configuration"
    assert error.value.retryable is False


@pytest.mark.parametrize(
    "values",
    [
        {
            "league_id": "529250970",
            "season": "2026",
            "espn_s2": "cookie",
        },
        {
            "league_id": "529250970",
            "season": "2026",
            "swid": "{swid}",
        },
    ],
)
def test_partial_private_credentials_are_rejected(
    values: dict[str, str],
) -> None:
    with pytest.raises(ProviderConfigurationError) as error:
        parse_espn_configuration(ProviderCredentials(values=values))

    assert "both espn_s2 and swid" in str(error.value)


def test_client_factory_receives_validated_values() -> None:
    calls: list[dict[str, Any]] = []

    def factory(
        *,
        league_id: int,
        year: int,
        espn_s2: str | None,
        swid: str | None,
    ) -> FakeLeague:
        calls.append(
            {
                "league_id": league_id,
                "year": year,
                "espn_s2": espn_s2,
                "swid": swid,
            }
        )

        return FakeLeague(
            league_id=league_id,
            year=year,
            espn_s2=espn_s2,
            swid=swid,
        )

    league = create_espn_client(
        ProviderCredentials(
            values={
                "league_id": "529250970",
                "season": "2026",
                "espn_s2": "cookie",
                "swid": "{swid}",
            }
        ),
        league_factory=factory,
    )

    assert isinstance(league, FakeLeague)
    assert calls == [
        {
            "league_id": 529250970,
            "year": 2026,
            "espn_s2": "cookie",
            "swid": "{swid}",
        }
    ]


def test_credentials_from_values_normalizes_input() -> None:
    credentials = credentials_from_values(
        league_id=529250970,
        season=2026,
        espn_s2=" cookie ",
        swid=" {swid} ",
    )

    assert dict(credentials.values) == {
        "league_id": "529250970",
        "season": "2026",
        "espn_s2": "cookie",
        "swid": "{swid}",
    }


def test_configuration_is_frozen() -> None:
    configuration = EspnConfiguration(
        league_id=529250970,
        season=2026,
        espn_s2=None,
        swid=None,
    )

    with pytest.raises(FrozenInstanceError):
        configuration.season = 2027


def test_safe_errors_do_not_include_secrets() -> None:
    secret = "private-cookie-value"

    with pytest.raises(ProviderConfigurationError) as error:
        parse_espn_configuration(
            ProviderCredentials(
                values={
                    "league_id": "529250970",
                    "season": "2026",
                    "espn_s2": secret,
                }
            )
        )

    assert secret not in str(error.value)
