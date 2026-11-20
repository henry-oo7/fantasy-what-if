from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.domain import (
    ProviderCredentials,
    ProviderValidationResult,
)
from app.providers.registry import ProviderRegistry
from app.services.provider_credential_replacement import (
    ProviderCredentialReplacementUnavailableError,
    ProviderCredentialReplacementValidationError,
    replace_provider_credentials,
)

NOW = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)


class FakeProvider:
    provider = FantasyProvider.ESPN

    def __init__(
        self,
        *,
        is_valid: bool = True,
        returned_league_id: str = "529250970",
        returned_season_id: str = "2026",
    ) -> None:
        self.is_valid = is_valid
        self.returned_league_id = returned_league_id
        self.returned_season_id = returned_season_id
        self.fetch_calls: list[tuple[str, str]] = []

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        return ProviderValidationResult(
            is_valid=self.is_valid,
            provider=self.provider,
            safe_message=(
                "ESPN configuration is valid."
                if self.is_valid
                else "The fantasy provider credentials were rejected."
            ),
        )

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ):
        self.fetch_calls.append(
            (
                provider_league_id,
                provider_season_id,
            )
        )
        return SimpleNamespace(
            provider_league_id=self.returned_league_id,
            provider_season_id=self.returned_season_id,
        )

    def discover_leagues(self):
        raise AssertionError("Credential replacement must not use league discovery.")

    def fetch_matchups(self, **_):
        raise AssertionError("Credential replacement must not synchronize matchups.")

    def fetch_weekly_rosters(self, **_):
        raise AssertionError("Credential replacement must not synchronize rosters.")


def credential_keyring(
    key: str,
    *,
    current_version: int = 1,
    historical_keys: dict[int, str] | None = None,
) -> ProviderCredentialKeyring:
    keys = dict(historical_keys or {})
    keys[current_version] = key

    return ProviderCredentialKeyring(
        keys=keys,
        current_version=current_version,
    )


def replacement_credentials() -> ProviderCredentials:
    return ProviderCredentials(
        values={
            "league_id": "attacker-controlled-league",
            "season": "1900",
            "espn_s2": "replacement-cookie",
            "swid": "{replacement-swid}",
        }
    )


def registry_with(
    provider: FakeProvider,
    captured: dict[str, str],
) -> ProviderRegistry:
    registry = ProviderRegistry()

    def factory(
        credentials: ProviderCredentials,
    ) -> FakeProvider:
        captured.update(credentials.values)
        return provider

    registry.register(
        FantasyProvider.ESPN,
        factory,
    )
    return registry


def context_objects():
    installation = Installation(
        id=1,
        application_name="Fantasy What-If",
        status=InstallationStatus.ACTIVE,
        active_league_id=10,
        active_season_id=20,
    )
    league = League(
        id=10,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        provider_league_id="529250970",
        name="Claimed ESPN League",
    )
    season = Season(
        id=20,
        league_id=10,
        year=2026,
        current_week=10,
        regular_season_weeks=14,
        provider_connection_id=30,
        provider_season_id="2026",
        status=SeasonStatus.ACTIVE,
    )
    connection = ProviderConnection(
        id=30,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        display_name="ESPN Fantasy Football",
        encrypted_credentials="existing-envelope",
        credential_version=1,
        credential_schema_version=1,
        is_active=True,
        last_validated_at=(datetime(2026, 11, 1, tzinfo=UTC)),
        last_validation_error=None,
    )
    claim = ProviderLeagueClaim(
        id=40,
        workspace_id=1,
        league_id=10,
        provider=FantasyProvider.ESPN,
        provider_league_id="529250970",
        normalized_provider_league_id="529250970",
    )

    return (
        installation,
        league,
        season,
        connection,
        claim,
    )


def session_for(
    *,
    installation,
    league,
    season,
    connection,
    claim,
) -> MagicMock:
    session = MagicMock()

    def get(model, identifier, **_):
        values = {
            (Installation, 1): installation,
            (League, 10): league,
            (Season, 20): season,
        }
        return values.get((model, identifier))

    session.get.side_effect = get
    session.scalar.side_effect = [
        connection,
        claim,
    ]
    return session


def test_replacement_validates_identity_before_atomic_update() -> None:
    (
        installation,
        league,
        season,
        connection,
        claim,
    ) = context_objects()
    session = session_for(
        installation=installation,
        league=league,
        season=season,
        connection=connection,
        claim=claim,
    )
    key = Fernet.generate_key().decode("ascii")
    captured: dict[str, str] = {}
    provider = FakeProvider()

    result = replace_provider_credentials(
        session,
        workspace_id=1,
        submitted_credentials=replacement_credentials(),
        credential_keyring=credential_keyring(key),
        registry=registry_with(provider, captured),
        now=NOW,
    )

    assert captured["league_id"] == "529250970"
    assert captured["season"] == "2026"
    assert captured["espn_s2"] == "replacement-cookie"
    assert captured["swid"] == "{replacement-swid}"
    assert "attacker-controlled-league" not in captured.values()
    assert "1900" not in captured.values()

    assert provider.fetch_calls == [
        (
            "529250970",
            "2026",
        )
    ]

    assert connection.encrypted_credentials != ("existing-envelope")
    assert connection.credential_version == 1
    assert connection.credential_schema_version == 1
    assert connection.last_validated_at == NOW
    assert connection.last_validation_error is None

    envelope = ProviderCredentialVault(keys={1: key}).decrypt(
        encrypted_credentials=connection.encrypted_credentials,
        key_version=1,
        expected_provider=FantasyProvider.ESPN,
        expected_schema_version=1,
    )

    assert dict(envelope.credentials.values) == captured
    assert result.workspace_id == 1
    assert result.connection_id == 30
    assert result.provider == "espn"
    assert result.provider_league_id == "529250970"
    assert result.provider_season_id == "2026"
    assert result.validated_at == NOW
    session.flush.assert_called_once_with()


def test_invalid_credentials_preserve_existing_envelope() -> None:
    (
        installation,
        league,
        season,
        connection,
        claim,
    ) = context_objects()
    session = session_for(
        installation=installation,
        league=league,
        season=season,
        connection=connection,
        claim=claim,
    )
    original_validated_at = connection.last_validated_at
    captured: dict[str, str] = {}

    with pytest.raises(
        ProviderCredentialReplacementValidationError,
        match="credentials were rejected",
    ):
        replace_provider_credentials(
            session,
            workspace_id=1,
            submitted_credentials=replacement_credentials(),
            credential_keyring=credential_keyring(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeProvider(is_valid=False),
                captured,
            ),
            now=NOW,
        )

    assert connection.encrypted_credentials == ("existing-envelope")
    assert connection.last_validated_at == original_validated_at
    assert connection.last_validation_error is None
    session.flush.assert_not_called()


def test_different_league_preserves_existing_envelope() -> None:
    (
        installation,
        league,
        season,
        connection,
        claim,
    ) = context_objects()
    session = session_for(
        installation=installation,
        league=league,
        season=season,
        connection=connection,
        claim=claim,
    )

    with pytest.raises(
        ProviderCredentialReplacementValidationError,
        match="do not access the claimed league",
    ):
        replace_provider_credentials(
            session,
            workspace_id=1,
            submitted_credentials=replacement_credentials(),
            credential_keyring=credential_keyring(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeProvider(
                    returned_league_id="different-league",
                ),
                {},
            ),
            now=NOW,
        )

    assert connection.encrypted_credentials == ("existing-envelope")
    assert connection.credential_version == 1
    assert connection.credential_schema_version == 1
    session.flush.assert_not_called()


def test_different_season_preserves_existing_envelope() -> None:
    (
        installation,
        league,
        season,
        connection,
        claim,
    ) = context_objects()
    session = session_for(
        installation=installation,
        league=league,
        season=season,
        connection=connection,
        claim=claim,
    )

    with pytest.raises(
        ProviderCredentialReplacementValidationError,
        match="do not access the active season",
    ):
        replace_provider_credentials(
            session,
            workspace_id=1,
            submitted_credentials=replacement_credentials(),
            credential_keyring=credential_keyring(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeProvider(
                    returned_season_id="2025",
                ),
                {},
            ),
            now=NOW,
        )

    assert connection.encrypted_credentials == ("existing-envelope")
    session.flush.assert_not_called()


def test_claim_must_belong_to_active_workspace_and_league() -> None:
    (
        installation,
        league,
        season,
        connection,
        claim,
    ) = context_objects()
    claim.workspace_id = 2
    session = session_for(
        installation=installation,
        league=league,
        season=season,
        connection=connection,
        claim=None,
    )

    with pytest.raises(
        ProviderCredentialReplacementUnavailableError,
        match="claim is unavailable",
    ):
        replace_provider_credentials(
            session,
            workspace_id=1,
            submitted_credentials=replacement_credentials(),
            credential_keyring=credential_keyring(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeProvider(),
                {},
            ),
        )

    assert connection.encrypted_credentials == ("existing-envelope")
    session.flush.assert_not_called()


def test_missing_active_context_is_rejected() -> None:
    session = MagicMock()
    session.get.return_value = None

    with pytest.raises(
        ProviderCredentialReplacementUnavailableError,
        match="does not exist",
    ):
        replace_provider_credentials(
            session,
            workspace_id=999,
            submitted_credentials=replacement_credentials(),
            credential_keyring=credential_keyring(Fernet.generate_key().decode("ascii")),
            registry=registry_with(
                FakeProvider(),
                {},
            ),
        )

    session.flush.assert_not_called()


def test_replacement_upgrades_to_current_key_version() -> None:
    (
        installation,
        league,
        season,
        connection,
        claim,
    ) = context_objects()
    session = session_for(
        installation=installation,
        league=league,
        season=season,
        connection=connection,
        claim=claim,
    )
    first_key = Fernet.generate_key().decode("ascii")
    second_key = Fernet.generate_key().decode("ascii")

    result = replace_provider_credentials(
        session,
        workspace_id=1,
        submitted_credentials=replacement_credentials(),
        credential_keyring=credential_keyring(
            second_key,
            current_version=2,
            historical_keys={
                1: first_key,
            },
        ),
        registry=registry_with(
            FakeProvider(),
            {},
        ),
        now=NOW,
    )

    assert connection.credential_version == 2
    assert result.credential_version == 2

    envelope = ProviderCredentialVault(
        keys={
            1: first_key,
            2: second_key,
        }
    ).decrypt(
        encrypted_credentials=connection.encrypted_credentials,
        key_version=2,
        expected_provider=FantasyProvider.ESPN,
        expected_schema_version=1,
    )

    assert envelope.credentials.values["espn_s2"] == ("replacement-cookie")
