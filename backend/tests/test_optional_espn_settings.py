import pytest
from pydantic import SecretStr

from app.core.settings import Settings


def test_espn_secrets_default_to_none() -> None:
    assert Settings.model_fields["espn_s2"].default is None
    assert Settings.model_fields["espn_swid"].default is None


def test_settings_load_without_espn_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "ESPN_S2",
        raising=False,
    )
    monkeypatch.delenv(
        "ESPN_SWID",
        raising=False,
    )

    settings = Settings(
        _env_file=None,
        app_secret_key=SecretStr("x" * 48),
        frontend_origin=("http://localhost:5173"),
        trusted_origins=("http://localhost:5173"),
        database_url=("postgresql+psycopg://user:password@localhost/fantasy"),
    )

    assert settings.espn_s2 is None
    assert settings.espn_swid is None


@pytest.mark.parametrize(
    (
        "espn_s2",
        "espn_swid",
    ),
    [
        (
            SecretStr(""),
            SecretStr(""),
        ),
        (
            SecretStr("   "),
            SecretStr("   "),
        ),
    ],
)
def test_blank_espn_secrets_become_none(
    espn_s2: SecretStr,
    espn_swid: SecretStr,
) -> None:
    settings = Settings(
        _env_file=None,
        app_secret_key=SecretStr("x" * 48),
        frontend_origin=("http://localhost:5173"),
        trusted_origins=("http://localhost:5173"),
        espn_s2=espn_s2,
        espn_swid=espn_swid,
        database_url=("postgresql+psycopg://user:password@localhost/fantasy"),
    )

    assert settings.espn_s2 is None
    assert settings.espn_swid is None


def test_optional_swid_is_still_normalized() -> None:
    settings = Settings(
        _env_file=None,
        app_secret_key=SecretStr("x" * 48),
        frontend_origin=("http://localhost:5173"),
        trusted_origins=("http://localhost:5173"),
        espn_s2=SecretStr("cookie"),
        espn_swid=SecretStr("swid"),
        database_url=("postgresql+psycopg://user:password@localhost/fantasy"),
    )

    assert settings.espn_swid is not None
    assert settings.espn_swid.get_secret_value() == "{swid}"
