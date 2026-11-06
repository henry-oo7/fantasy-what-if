from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import (
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    app_env: Literal[
        "development",
        "test",
        "production",
    ] = "development"
    app_debug: bool = True
    app_log_level: str = "INFO"
    app_secret_key: SecretStr

    frontend_origin: str = "http://localhost:5173"
    trusted_origins: str = "http://localhost:5173"

    session_cookie_name: str = "fantasy_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal[
        "lax",
        "strict",
        "none",
    ] = "lax"
    session_lifetime_hours: int = Field(
        default=168,
        ge=1,
        le=24 * 30,
    )
    invite_lifetime_hours: int = Field(
        default=168,
        ge=1,
        le=24 * 30,
    )

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

    @field_validator(
        "app_secret_key",
        "espn_s2",
        "espn_swid",
    )
    @classmethod
    def normalize_secrets(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        secret = value.get_secret_value().strip()

        if not secret:
            raise ValueError("Secret values cannot be empty.")

        return SecretStr(secret)

    @field_validator("espn_swid")
    @classmethod
    def normalize_swid(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        swid = value.get_secret_value()

        if not swid.startswith("{"):
            swid = "{" + swid

        if not swid.endswith("}"):
            swid = swid + "}"

        return SecretStr(swid)

    @field_validator(
        "frontend_origin",
        "trusted_origins",
    )
    @classmethod
    def strip_origin_values(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("Origin configuration cannot be empty.")

        return normalized

    @field_validator("session_cookie_name")
    @classmethod
    def validate_cookie_name(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("Session cookie name cannot be empty.")

        return normalized

    @property
    def trusted_origin_list(self) -> tuple[str, ...]:
        return tuple(
            origin.strip().rstrip("/")
            for origin in self.trusted_origins.split(",")
            if origin.strip()
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def validate_environment_security(self) -> Settings:
        secret = self.app_secret_key.get_secret_value()
        frontend_origin = self.frontend_origin.rstrip("/")
        origins = self.trusted_origin_list

        if "*" in origins:
            raise ValueError("Wildcard trusted origins are not allowed.")

        if frontend_origin not in origins:
            raise ValueError("FRONTEND_ORIGIN must also appear in TRUSTED_ORIGINS.")

        for origin in origins:
            parsed = urlparse(origin)

            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Invalid trusted origin: {origin}")

        if not self.is_production:
            return self

        if self.app_debug:
            raise ValueError("APP_DEBUG must be false in production.")

        if len(secret) < 32:
            raise ValueError("APP_SECRET_KEY must contain at least 32 characters in production.")

        if "replace" in secret.casefold():
            raise ValueError("APP_SECRET_KEY cannot contain a placeholder in production.")

        if not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE must be true in production.")

        if self.session_cookie_name != "__Host-fantasy_session":
            raise ValueError("Production must use the __Host-fantasy_session cookie name.")

        if self.session_cookie_samesite == "none":
            raise ValueError("SameSite=None is not required for this same-site deployment.")

        if not frontend_origin.startswith("https://"):
            raise ValueError("FRONTEND_ORIGIN must use HTTPS in production.")

        if any(not origin.startswith("https://") for origin in origins):
            raise ValueError("Every production trusted origin must use HTTPS.")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
