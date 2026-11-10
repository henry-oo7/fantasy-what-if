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
    ProviderLeagueReference,
    ProviderScheduleRules,
    ProviderValidationResult,
)
from app.providers.registry import ProviderRegistry
from app.services.setup_finalization import (
    SETUP_FINALIZATION_LOCK_KEY,
    SetupFinalizationUnavailableError,
    finalize_installation_setup,
)


class FakeProvider:
    provider = FantasyProvider.ESPN

    def __init__(
        self,
        *,
        season_year: int = 2026,
    ) -> None:
        self.season_year = season_year

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
                provider_season_id=str(self.season_year),
                name="Jimmy and Friends",
                sport=FantasySport.FOOTBALL,
                season_year=self.season_year,
                logo_url=None,
            ),
        )

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> ProviderLeague:
        assert provider_league_id == "529250970"
        assert provider_season_id == str(self.season_year)

        return ProviderLeague(
            provider_league_id="529250970",
            provider_season_id=str(self.season_year),
            name="Jimmy and Friends",
            sport=FantasySport.FOOTBALL,
            season_year=self.season_year,
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
                supports_lineup_history=True,
                supports_optimal_lineups=False,
                supports_schedule_swap=True,
                supports_all_play=True,
                supports_league_average=True,
                supports_team_logos=False,
            ),
        )

    def fetch_matchups(self, **_):
        raise AssertionError("Synchronization is mocked in this unit test.")

    def fetch_weekly_rosters(self, **_):
        raise AssertionError("Synchronization is mocked in this unit test.")


def registry(
    *,
    season_year: int = 2026,
) -> ProviderRegistry:
    result = ProviderRegistry()

    result.register(
        FantasyProvider.ESPN,
        lambda _: FakeProvider(
            season_year=season_year,
        ),
    )

    return result


def setup_objects(
    *,
    installation_status: InstallationStatus = (InstallationStatus.SETUP_IN_PROGRESS),
):
    key = Fernet.generate_key().decode("ascii")

    credentials = ProviderCredentials(
        values={
            "league_id": "529250970",
            "season": "2026",
            "espn_s2": "private-cookie",
            "swid": "{private-swid}",
        }
    )

    encrypted = ProviderCredentialVault(keys={1: key}).encrypt(
        provider=FantasyProvider.ESPN,
        credentials=credentials,
        key_version=1,
    )

    installation = Installation(
        id=1,
        application_name="Fantasy What-If",
        timezone="America/Chicago",
        status=installation_status,
    )

    connection = ProviderConnection(
        id=1,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        display_name="ESPN",
        encrypted_credentials=encrypted,
        credential_version=1,
        is_active=True,
    )

    return key, installation, connection


def setup_session(
    installation: Installation,
    connection: ProviderConnection,
) -> MagicMock:
    session = MagicMock()

    session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[installation])),
        MagicMock(all=MagicMock(return_value=[connection])),
    ]

    session.scalar.return_value = None

    identifiers = iter((8, 9))

    def add(value) -> None:
        if isinstance(value, (League, Season)):
            value.id = next(identifiers)

    session.add.side_effect = add

    return session


def test_finalization_activates_after_full_sync() -> None:
    key, installation, connection = setup_objects()
    session = setup_session(
        installation,
        connection,
    )
    timestamp = datetime(2026, 11, 9, tzinfo=UTC)

    sync_result = SimpleNamespace(
        season_id=9,
    )

    with patch(
        "app.services.setup_finalization.synchronize_provider",
        return_value=sync_result,
    ) as synchronize:
        result = finalize_installation_setup(
            session,
            credential_key=key,
            registry=registry(),
            now=timestamp,
        )

    assert result.installation_id == 1
    assert result.league_id == 8
    assert result.season_id == 9
    assert result.sync_result is sync_result

    assert installation.status is InstallationStatus.ACTIVE
    assert installation.active_league_id == 8
    assert installation.active_season_id == 9
    assert installation.setup_completed_at == timestamp

    added = [call.args[0] for call in session.add.call_args_list]

    league = next(value for value in added if isinstance(value, League))
    season = next(value for value in added if isinstance(value, Season))

    assert league.provider_league_id == "529250970"
    assert season.provider_season_id == "2026"
    assert season.status is SeasonStatus.ACTIVE
    assert season.activated_at == timestamp

    synchronize.assert_called_once()

    sync_call = synchronize.call_args.kwargs

    assert sync_call["session"] is session
    assert sync_call["season_id"] == 9
    assert isinstance(
        sync_call["provider"],
        FakeProvider,
    )
    assert sync_call["full_sync"] is True
    assert sync_call["commit_transaction"] is False

    session.commit.assert_called_once()

    statement = str(session.execute.call_args.args[0])

    assert "pg_advisory_xact_lock" in statement
    assert session.execute.call_args.args[1] == {
        "lock_key": SETUP_FINALIZATION_LOCK_KEY,
    }


def test_active_installation_cannot_be_finalized() -> None:
    key, installation, connection = setup_objects(
        installation_status=InstallationStatus.ACTIVE,
    )
    session = setup_session(
        installation,
        connection,
    )

    with pytest.raises(
        SetupFinalizationUnavailableError,
        match="setup is in progress",
    ):
        finalize_installation_setup(
            session,
            credential_key=key,
            registry=registry(),
        )

    session.commit.assert_not_called()


def test_existing_league_blocks_finalization() -> None:
    key, installation, connection = setup_objects()
    session = setup_session(
        installation,
        connection,
    )
    session.scalar.return_value = 22

    with pytest.raises(
        SetupFinalizationUnavailableError,
        match="already configured",
    ):
        finalize_installation_setup(
            session,
            credential_key=key,
            registry=registry(),
        )

    session.commit.assert_not_called()


def test_historical_finalization_completes_without_active_sync_season() -> None:
    key, installation, connection = setup_objects()
    session = setup_session(
        installation,
        connection,
    )
    timestamp = datetime(
        2026,
        11,
        10,
        tzinfo=UTC,
    )

    with patch(
        "app.services.setup_finalization.synchronize_provider",
        return_value=SimpleNamespace(
            season_id=9,
        ),
    ):
        finalize_installation_setup(
            session,
            credential_key=key,
            registry=registry(
                season_year=2025,
            ),
            now=timestamp,
        )

    added = [call.args[0] for call in session.add.call_args_list]
    season = next(value for value in added if isinstance(value, Season))

    assert installation.status is InstallationStatus.ACTIVE
    assert installation.active_league_id == 8
    assert installation.active_season_id is None
    assert season.status is SeasonStatus.COMPLETED
    assert season.activated_at is None
    assert season.completed_at == timestamp


def test_future_finalization_is_rejected_before_database_inserts() -> None:
    key, installation, connection = setup_objects()
    session = setup_session(
        installation,
        connection,
    )
    timestamp = datetime(
        2026,
        11,
        10,
        tzinfo=UTC,
    )

    with pytest.raises(
        Exception,
        match=("Season 2027 cannot be imported before the 2027 calendar year"),
    ):
        finalize_installation_setup(
            session,
            credential_key=key,
            registry=registry(
                season_year=2027,
            ),
            now=timestamp,
        )

    session.add.assert_not_called()
    session.commit.assert_not_called()
