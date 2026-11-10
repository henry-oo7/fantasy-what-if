from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
)
from app.db.platform_models import Installation
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.domain import (
    ProviderCredentials,
    ProviderValidationResult,
)
from app.providers.registry import ProviderRegistry
from app.services.setup_provider_connection import (
    PROVIDER_CREDENTIAL_KEY_VERSION,
    SETUP_PROVIDER_LOCK_KEY,
    SetupProviderAlreadyConfiguredError,
    SetupProviderConnectionUnavailableError,
    SetupProviderValidationError,
    configure_provider_connection,
)


class FakeAdapter:
    provider = FantasyProvider.ESPN

    def __init__(
        self,
        *,
        is_valid: bool,
        safe_message: str,
    ) -> None:
        self.is_valid = is_valid
        self.safe_message = safe_message

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        return ProviderValidationResult(
            is_valid=self.is_valid,
            provider=self.provider,
            safe_message=self.safe_message,
        )

    def discover_leagues(self):
        raise AssertionError("Provider discovery is not used by this test.")

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ):
        raise AssertionError("League retrieval is not used by this test.")

    def fetch_matchups(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ):
        raise AssertionError("Matchup retrieval is not used by this test.")

    def fetch_weekly_rosters(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ):
        raise AssertionError("Roster retrieval is not used by this test.")


def credentials() -> ProviderCredentials:
    return ProviderCredentials(
        values={
            "league_id": "529250970",
            "season": "2026",
            "espn_s2": "private-cookie",
            "swid": "{private-swid}",
        }
    )


def installation(
    *,
    status: InstallationStatus = (InstallationStatus.SETUP_IN_PROGRESS),
) -> Installation:
    return Installation(
        id=1,
        application_name="Fantasy What-If",
        timezone="America/Chicago",
        status=status,
    )


def registry_with(
    adapter: FakeAdapter,
) -> ProviderRegistry:
    registry = ProviderRegistry()

    registry.register(
        FantasyProvider.ESPN,
        lambda _: adapter,
    )

    return registry


def setup_session(
    *,
    installations: list[Installation],
    existing_connection_id: int | None = None,
) -> MagicMock:
    session = MagicMock()

    session.scalars.return_value.all.return_value = installations
    session.scalar.return_value = existing_connection_id

    def assign_identifier(value) -> None:
        value.id = 10

    session.add.side_effect = assign_identifier

    return session


def test_valid_credentials_are_encrypted_and_stored() -> None:
    session = setup_session(
        installations=[installation()],
    )
    key = Fernet.generate_key().decode("ascii")
    timestamp = datetime(2026, 11, 9, tzinfo=UTC)

    connection = configure_provider_connection(
        session,
        provider=FantasyProvider.ESPN,
        display_name="  ESPN   League  ",
        credentials=credentials(),
        credential_key=key,
        registry=registry_with(
            FakeAdapter(
                is_valid=True,
                safe_message=("ESPN configuration is valid."),
            )
        ),
        now=timestamp,
    )

    assert connection.id == 10
    assert connection.installation_id == 1
    assert connection.provider is FantasyProvider.ESPN
    assert connection.display_name == "ESPN League"
    assert connection.credential_version == 1
    assert connection.is_active is True
    assert connection.last_validated_at == timestamp
    assert connection.last_validation_error is None

    assert connection.encrypted_credentials is not None
    assert "private-cookie" not in connection.encrypted_credentials
    assert "{private-swid}" not in connection.encrypted_credentials

    envelope = ProviderCredentialVault(
        keys={
            PROVIDER_CREDENTIAL_KEY_VERSION: key,
        }
    ).decrypt(
        encrypted_credentials=(connection.encrypted_credentials),
        key_version=connection.credential_version,
        expected_provider=FantasyProvider.ESPN,
    )

    assert dict(envelope.credentials.values) == {
        "espn_s2": "private-cookie",
        "league_id": "529250970",
        "season": "2026",
        "swid": "{private-swid}",
    }

    session.add.assert_called_once_with(connection)
    session.flush.assert_called_once()

    statement_text = str(session.execute.call_args.args[0])

    assert "pg_advisory_xact_lock" in statement_text
    assert session.execute.call_args.args[1] == {
        "lock_key": SETUP_PROVIDER_LOCK_KEY,
    }


def test_invalid_provider_configuration_is_not_stored() -> None:
    session = setup_session(
        installations=[installation()],
    )

    with pytest.raises(
        SetupProviderValidationError,
        match="could not load",
    ):
        configure_provider_connection(
            session,
            provider=FantasyProvider.ESPN,
            display_name="ESPN",
            credentials=credentials(),
            credential_key=(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeAdapter(
                    is_valid=False,
                    safe_message=("ESPN could not load the configured league."),
                )
            ),
        )

    session.add.assert_not_called()


def test_existing_connection_is_rejected() -> None:
    session = setup_session(
        installations=[installation()],
        existing_connection_id=7,
    )

    with pytest.raises(
        SetupProviderAlreadyConfiguredError,
        match="already configured",
    ):
        configure_provider_connection(
            session,
            provider=FantasyProvider.ESPN,
            display_name="ESPN",
            credentials=credentials(),
            credential_key=(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeAdapter(
                    is_valid=True,
                    safe_message="Valid.",
                )
            ),
        )


@pytest.mark.parametrize(
    "installations",
    [
        [],
        [
            installation(),
            Installation(
                id=2,
                application_name="Duplicate",
                timezone="America/Chicago",
                status=(InstallationStatus.SETUP_IN_PROGRESS),
            ),
        ],
    ],
)
def test_exactly_one_installation_is_required(
    installations: list[Installation],
) -> None:
    session = setup_session(
        installations=installations,
    )

    with pytest.raises(
        SetupProviderConnectionUnavailableError,
        match="Exactly one installation",
    ):
        configure_provider_connection(
            session,
            provider=FantasyProvider.ESPN,
            display_name="ESPN",
            credentials=credentials(),
            credential_key=(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeAdapter(
                    is_valid=True,
                    safe_message="Valid.",
                )
            ),
        )


def test_active_installation_rejects_setup_connection() -> None:
    session = setup_session(
        installations=[
            installation(
                status=InstallationStatus.ACTIVE,
            )
        ],
    )

    with pytest.raises(
        SetupProviderConnectionUnavailableError,
        match="setup is in progress",
    ):
        configure_provider_connection(
            session,
            provider=FantasyProvider.ESPN,
            display_name="ESPN",
            credentials=credentials(),
            credential_key=(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeAdapter(
                    is_valid=True,
                    safe_message="Valid.",
                )
            ),
        )
