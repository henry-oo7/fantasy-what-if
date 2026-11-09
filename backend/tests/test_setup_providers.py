from app.db.platform_enums import FantasyProvider
from app.services.setup_providers import (
    list_setup_providers,
)


def test_setup_providers_use_stable_order() -> None:
    providers = list_setup_providers()

    assert tuple(provider.provider for provider in providers) == (
        FantasyProvider.ESPN,
        FantasyProvider.SLEEPER,
        FantasyProvider.YAHOO,
    )


def test_only_registered_provider_is_implemented() -> None:
    providers = {provider.provider: provider for provider in list_setup_providers()}

    assert providers[FantasyProvider.ESPN].implemented is True

    assert providers[FantasyProvider.SLEEPER].implemented is False

    assert providers[FantasyProvider.YAHOO].implemented is False


def test_espn_setup_fields_are_explicit() -> None:
    espn = next(
        provider for provider in list_setup_providers() if provider.provider is FantasyProvider.ESPN
    )

    assert espn.display_name == ("ESPN Fantasy Football")
    assert espn.requires_credentials is True
    assert espn.credential_fields == (
        "league_id",
        "season",
        "espn_s2",
        "swid",
    )


def test_unimplemented_providers_expose_no_fake_fields() -> None:
    providers = list_setup_providers()

    for provider in providers:
        if not provider.implemented:
            assert provider.credential_fields == ()
