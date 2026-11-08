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

    sync_scheduler_enabled: bool = False
    sync_schedule_timezone: str = "America/Chicago"
    sync_schedule_weekday: int = Field(
        default=2,
        ge=0,
        le=6,
    )
    sync_schedule_hour: int = Field(
        default=6,
        ge=0,
        le=23,
    )
    sync_schedule_minute: int = Field(
        default=0,
        ge=0,
        le=59,
    )
    sync_scheduler_poll_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
    )

    espn_s2: SecretStr | None = None
    espn_swid: SecretStr | None = None

    provider_credential_key: SecretStr | None = None

    database_url: str
    database_echo: bool = False

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("app_secret_key")
    @classmethod
    def normalize_app_secret(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        secret = value.get_secret_value().strip()

        if not secret:
            raise ValueError("APP_SECRET_KEY cannot be empty.")

        return SecretStr(secret)

    @field_validator(
        "espn_s2",
        "espn_swid",
    )
    @classmethod
    def normalize_optional_espn_secret(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        if value is None:
            return None

        secret = value.get_secret_value().strip()

        if not secret:
            return None

        return SecretStr(secret)

    @field_validator("provider_credential_key")
    @classmethod
    def validate_provider_credential_key(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        """Validate an optional Fernet provider credential key."""

        if value is None:
            return None

        normalized = value.get_secret_value().strip()

        if not normalized:
            return None

        try:
            from cryptography.fernet import Fernet

            Fernet(normalized.encode("ascii"))
        except (
            TypeError,
            ValueError,
            UnicodeEncodeError,
        ) as exc:
            raise ValueError("PROVIDER_CREDENTIAL_KEY must be a valid Fernet key.") from exc

        return SecretStr(normalized)

    @field_validator("espn_swid")
    @classmethod
    def normalize_swid(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        if value is None:
            return None

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
