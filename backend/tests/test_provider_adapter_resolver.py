from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.providers.errors import (
    ProviderNotSupportedError,
)
from app.providers.espn import EspnProvider
from app.providers.registry import ProviderRegistry
from app.services.provider_adapter_resolver import (
    ProviderAdapterResolutionError,
    build_provider_registry,
    resolve_connection_credentials,
)


def settings(
    *,
    key: str | None = None,
):
    return SimpleNamespace(
        provider_credential_key=(SecretStr(key) if key is not None else None),
        espn_s2=SecretStr("environment-cookie"),
        espn_swid=SecretStr("{environment-swid}"),
    )


def season():
    return SimpleNamespace(
        provider_season_id="2026",
        league=SimpleNamespace(
            provider=FantasyProvider.ESPN,
            provider_league_id="529250970",
        ),
    )


def connection(
    *,
    encrypted_credentials: str | None = None,
    provider: FantasyProvider = (FantasyProvider.ESPN),
    credential_version: int = 1,
):
    return SimpleNamespace(
        provider=provider,
        encrypted_credentials=(encrypted_credentials),
        credential_version=credential_version,
    )


def test_registry_contains_espn() -> None:
    registry = build_provider_registry()

    assert registry.registered_providers() == (FantasyProvider.ESPN,)

    adapter = registry.create(
        FantasyProvider.ESPN,
        ProviderCredentials(
            values={
                "league_id": "529250970",
                "season": "2026",
            }
        ),
    )

    assert isinstance(adapter, EspnProvider)


def test_environment_fallback_builds_espn_credentials() -> None:
    credentials, source = resolve_connection_credentials(
        connection=connection(),
        season=season(),
        settings=settings(),
    )

    assert source == "environment_fallback"
    assert dict(credentials.values) == {
        "espn_s2": "environment-cookie",
        "league_id": "529250970",
        "season": "2026",
        "swid": "{environment-swid}",
    }


def test_environment_fallback_requires_both_secrets() -> None:
    incomplete_settings = SimpleNamespace(
        provider_credential_key=None,
        espn_s2=None,
        espn_swid=None,
    )

    with pytest.raises(
        ProviderAdapterResolutionError,
        match="requires both",
    ):
        resolve_connection_credentials(
            connection=connection(),
            season=season(),
            settings=incomplete_settings,
        )


def test_stored_credentials_take_precedence() -> None:
    key = Fernet.generate_key().decode("ascii")
    vault = ProviderCredentialVault(
        keys={
            1: key,
        }
    )

    encrypted = vault.encrypt(
        provider=FantasyProvider.ESPN,
        credentials=ProviderCredentials(
            values={
                "league_id": "529250970",
                "season": "2026",
                "espn_s2": "stored-cookie",
                "swid": "{stored-swid}",
            }
        ),
        key_version=1,
    )

    credentials, source = resolve_connection_credentials(
        connection=connection(
            encrypted_credentials=encrypted,
        ),
        season=season(),
        settings=settings(key=key),
    )

    assert source == "stored"
    assert dict(credentials.values) == {
        "espn_s2": "stored-cookie",
        "league_id": "529250970",
        "season": "2026",
        "swid": "{stored-swid}",
    }


def test_stored_credentials_require_key() -> None:
    with pytest.raises(
        ProviderAdapterResolutionError,
        match="require",
    ):
        resolve_connection_credentials(
            connection=connection(
                encrypted_credentials="ciphertext",
            ),
            season=season(),
            settings=settings(),
        )


def test_corrupted_stored_credentials_are_safe() -> None:
    key = Fernet.generate_key().decode("ascii")

    with pytest.raises(
        ProviderAdapterResolutionError,
        match="could not be decrypted",
    ) as error:
        resolve_connection_credentials(
            connection=connection(
                encrypted_credentials="corrupted",
            ),
            season=season(),
            settings=settings(key=key),
        )

    assert "environment-cookie" not in str(error.value)


def test_non_espn_connection_requires_storage() -> None:
    with pytest.raises(
        ProviderAdapterResolutionError,
        match="does not have stored credentials",
    ):
        resolve_connection_credentials(
            connection=connection(
                provider=FantasyProvider.SLEEPER,
            ),
            season=season(),
            settings=settings(),
        )


def test_unregistered_provider_is_rejected() -> None:
    registry = ProviderRegistry()

    with pytest.raises(ProviderNotSupportedError):
        registry.create(
            FantasyProvider.SLEEPER,
            ProviderCredentials(
                values={
                    "token": "value",
                }
            ),
        )


def test_active_context_types_are_constructible() -> None:
    installation = Installation(
        id=1,
        application_name="Fantasy What If",
        status=InstallationStatus.ACTIVE,
        active_league_id=1,
        active_season_id=1,
    )
    provider_connection = ProviderConnection(
        id=1,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        display_name="ESPN",
        credential_version=1,
        is_active=True,
    )

    assert installation.active_season_id == 1
    assert provider_connection.provider is FantasyProvider.ESPN
