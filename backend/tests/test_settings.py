from pydantic import SecretStr

from app.core.settings import Settings


def test_swid_is_wrapped_in_braces() -> None:
    settings = Settings(
        espn_s2=SecretStr("test-s2"),
        espn_swid=SecretStr("test-swid"),
    )

    assert settings.espn_swid.get_secret_value() == "{test-swid}"
