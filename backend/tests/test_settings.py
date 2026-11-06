from pydantic import SecretStr

from app.core.settings import Settings


def test_swid_is_wrapped_in_braces() -> None:
    settings = Settings(
        espn_league_id=529250970,
        espn_season=2026,
        espn_s2=SecretStr("test-s2"),
        espn_swid=SecretStr("test-swid"),
    )

    assert settings.espn_swid.get_secret_value() == "{test-swid}"
