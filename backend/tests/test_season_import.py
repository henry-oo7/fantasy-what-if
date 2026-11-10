from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.domain import (
    ProviderCapabilities,
    ProviderCredentials,
    ProviderLeague,
    ProviderScheduleRules,
    ProviderValidationResult,
)
from app.providers.registry import ProviderRegistry
from app.services.season_import import (
    SEASON_IMPORT_LOCK_KEY,
    SeasonImportConflictError,
    SeasonImportValidationError,
    import_provider_season,
)

NOW = datetime(
    2026,
    11,
    10,
    tzinfo=UTC,
)


class FakeProvider:
    provider = FantasyProvider.ESPN

    def __init__(
        self,
        credentials: ProviderCredentials,
        *,
        returned_year: int,
    ) -> None:
        self.credentials = dict(credentials.values)
        self.returned_year = returned_year

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        return ProviderValidationResult(
            is_valid=True,
            provider=self.provider,
            safe_message="Configuration is valid.",
        )

    def discover_leagues(self) -> tuple:
        return ()

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> ProviderLeague:
        assert provider_league_id == "529250970"
        assert provider_season_id == str(self.returned_year)

        return ProviderLeague(
            provider_league_id="529250970",
            provider_season_id=str(self.returned_year),
            name="Jimmy and Friends",
            sport=FantasySport.FOOTBALL,
            season_year=self.returned_year,
            current_week=18,
            regular_season_weeks=14,
            playoff_week_start=None,
            playoff_week_end=None,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=(),
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=ProviderScheduleRules(
                regular_season_weeks=14,
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
                supports_lineup_history=True,
                supports_optimal_lineups=False,
                supports_schedule_swap=True,
                supports_all_play=True,
                supports_league_average=True,
                supports_team_logos=False,
            ),
        )

    def fetch_matchups(self, **_):
        raise AssertionError("Synchronization is mocked.")

    def fetch_weekly_rosters(self, **_):
        raise AssertionError("Synchronization is mocked.")


def registry(
    *,
    returned_year: int,
    captured: dict,
) -> ProviderRegistry:
    result = ProviderRegistry()

    def factory(
        credentials: ProviderCredentials,
    ) -> FakeProvider:
        provider = FakeProvider(
            credentials,
            returned_year=returned_year,
        )
        captured.update(provider.credentials)
        return provider

    result.register(
        FantasyProvider.ESPN,
        factory,
    )

    return result


def objects():
    key = Fernet.generate_key().decode("ascii")

    encrypted = ProviderCredentialVault(keys={1: key}).encrypt(
        provider=FantasyProvider.ESPN,
        credentials=ProviderCredentials(
            values={
                "league_id": "529250970",
                "season": "2024",
                "espn_s2": "private-cookie",
                "swid": "{private-swid}",
            }
        ),
        key_version=1,
    )

    installation = Installation(
        id=1,
        application_name="Fantasy What-If",
        status=InstallationStatus.ACTIVE,
        active_league_id=10,
        active_season_id=None,
    )

    league = League(
        id=10,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        provider_league_id="529250970",
        sport=FantasySport.FOOTBALL,
        name="Jimmy and Friends",
    )

    connection = ProviderConnection(
        id=15,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        display_name="ESPN Fantasy Football",
        encrypted_credentials=encrypted,
        credential_version=1,
        is_active=True,
    )

    return key, installation, league, connection


def configured_session(
    *,
    installation: Installation,
    league: League,
    connection: ProviderConnection,
    duplicate_season_id: int | None = None,
    previous_active: list[Season] | None = None,
) -> MagicMock:
    session = MagicMock()

    session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[installation])),
        MagicMock(all=MagicMock(return_value=[league])),
        MagicMock(all=MagicMock(return_value=[connection])),
        MagicMock(all=MagicMock(return_value=(previous_active or []))),
    ]

    session.scalar.return_value = duplicate_season_id

    def add(value) -> None:
        if isinstance(value, Season):
            value.id = 25

    session.add.side_effect = add

    return session


def test_historical_import_is_completed_without_active_sync() -> None:
    key, installation, league, connection = objects()
    session = configured_session(
        installation=installation,
        league=league,
        connection=connection,
    )
    captured = {}

    with patch(
        "app.services.season_import.synchronize_provider",
        return_value=SimpleNamespace(
            season_id=25,
        ),
    ):
        result = import_provider_season(
            session,
            season_year=2025,
            credential_key=key,
            registry=registry(
                returned_year=2025,
                captured=captured,
            ),
            now=NOW,
        )

    assert result.status is SeasonStatus.COMPLETED
    assert result.synchronization_enabled is False
    assert installation.active_season_id is None

    added = [call.args[0] for call in session.add.call_args_list]
    season = next(value for value in added if isinstance(value, Season))

    assert season.status is SeasonStatus.COMPLETED
    assert season.completed_at == NOW
    assert season.activated_at is None

    assert captured == {
        "league_id": "529250970",
        "season": "2025",
        "espn_s2": "private-cookie",
        "swid": "{private-swid}",
    }

    session.commit.assert_called_once()


def test_current_import_becomes_active() -> None:
    key, installation, league, connection = objects()
    previous = Season(
        id=20,
        league_id=10,
        provider_season_id="2025",
        year=2025,
        current_week=18,
        regular_season_weeks=14,
        status=SeasonStatus.ACTIVE,
    )
    session = configured_session(
        installation=installation,
        league=league,
        connection=connection,
        previous_active=[previous],
    )

    with patch(
        "app.services.season_import.synchronize_provider",
        return_value=SimpleNamespace(
            season_id=25,
        ),
    ):
        result = import_provider_season(
            session,
            season_year=2026,
            credential_key=key,
            registry=registry(
                returned_year=2026,
                captured={},
            ),
            now=NOW,
        )

    assert result.status is SeasonStatus.ACTIVE
    assert result.synchronization_enabled is True
    assert installation.active_season_id == 25
    assert previous.status is SeasonStatus.COMPLETED
    assert previous.completed_at == NOW


def test_duplicate_year_is_rejected_before_provider_creation() -> None:
    key, installation, league, connection = objects()
    session = configured_session(
        installation=installation,
        league=league,
        connection=connection,
        duplicate_season_id=20,
    )

    with pytest.raises(
        SeasonImportConflictError,
        match="already imported",
    ):
        import_provider_season(
            session,
            season_year=2025,
            credential_key=key,
            registry=registry(
                returned_year=2025,
                captured={},
            ),
            now=NOW,
        )

    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_future_year_is_rejected_before_database_access() -> None:
    session = MagicMock()

    with pytest.raises(
        SeasonImportValidationError,
        match=("Season 2027 cannot be imported before the 2027 calendar year"),
    ):
        import_provider_season(
            session,
            season_year=2027,
            credential_key="unused",
            now=NOW,
        )

    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_provider_returning_different_year_is_rejected() -> None:
    key, installation, league, connection = objects()
    session = configured_session(
        installation=installation,
        league=league,
        connection=connection,
    )

    with pytest.raises(
        AssertionError,
    ):
        import_provider_season(
            session,
            season_year=2025,
            credential_key=key,
            registry=registry(
                returned_year=2024,
                captured={},
            ),
            now=NOW,
        )

    session.commit.assert_not_called()


def test_import_uses_transaction_advisory_lock() -> None:
    key, installation, league, connection = objects()
    session = configured_session(
        installation=installation,
        league=league,
        connection=connection,
    )

    with patch(
        "app.services.season_import.synchronize_provider",
        return_value=SimpleNamespace(
            season_id=25,
        ),
    ):
        import_provider_season(
            session,
            season_year=2025,
            credential_key=key,
            registry=registry(
                returned_year=2025,
                captured={},
            ),
            now=NOW,
        )

    statement = str(session.execute.call_args_list[0].args[0])

    assert "pg_advisory_xact_lock" in statement
    assert session.execute.call_args_list[0].args[1] == {
        "lock_key": SEASON_IMPORT_LOCK_KEY,
    }
