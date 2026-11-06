from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    app_env: str = "development"
    app_log_level: str = "INFO"

    espn_league_id: int = Field(gt=0)
    espn_season: int = Field(ge=2018)
    espn_s2: SecretStr
    espn_swid: SecretStr

    database_url: str
    database_echo: bool = False

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("espn_s2", "espn_swid")
    @classmethod
    def normalize_credentials(cls, value: SecretStr) -> SecretStr:
        credential = value.get_secret_value().strip()

        if not credential:
            raise ValueError("ESPN credential cannot be empty")

        return SecretStr(credential)

    @field_validator("espn_swid")
    @classmethod
    def normalize_swid(cls, value: SecretStr) -> SecretStr:
        swid = value.get_secret_value()

        if not swid.startswith("{"):
            swid = "{" + swid

        if not swid.endswith("}"):
            swid = swid + "}"

        return SecretStr(swid)


@lru_cache
def get_settings() -> Settings:
    return Settings()
