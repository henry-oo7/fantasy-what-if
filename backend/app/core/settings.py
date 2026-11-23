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

from app.db.platform_enums import RegistrationMode

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
    forwarded_allow_ips: str = "127.0.0.1,::1"

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
    login_maximum_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
    )
    login_attempt_window_minutes: int = Field(
        default=15,
        ge=1,
        le=60,
    )
    login_lockout_minutes: int = Field(
        default=15,
        ge=1,
        le=24 * 60,
    )

    registration_mode: RegistrationMode = RegistrationMode.CLOSED
    registration_submission_maximum_requests: int = Field(
        default=5,
        ge=1,
        le=50,
    )
    registration_submission_window_minutes: int = Field(
        default=60,
        ge=1,
        le=24 * 60,
    )
    email_verification_lifetime_hours: int = Field(
        default=24,
        ge=1,
        le=24 * 7,
    )
    verification_resend_maximum_requests: int = Field(
        default=3,
        ge=1,
        le=20,
    )
    verification_resend_window_minutes: int = Field(
        default=60,
        ge=1,
        le=24 * 60,
    )
    password_reset_lifetime_minutes: int = Field(
        default=30,
        ge=5,
        le=24 * 60,
    )
    password_reset_maximum_requests: int = Field(
        default=3,
        ge=1,
        le=20,
    )
    password_reset_window_minutes: int = Field(
        default=60,
        ge=1,
        le=24 * 60,
    )

    provider_validation_workspace_maximum_requests: int = Field(
        default=10,
        ge=1,
        le=50,
    )
    provider_validation_source_maximum_requests: int = Field(
        default=30,
        ge=1,
        le=200,
    )
    provider_validation_window_minutes: int = Field(
        default=60,
        ge=1,
        le=24 * 60,
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

    background_worker_enabled: bool = False
    background_worker_poll_seconds: int = Field(
        default=5,
        ge=1,
        le=300,
    )
    background_worker_lease_seconds: int = Field(
        default=300,
        ge=30,
        le=60 * 60,
    )
    background_worker_heartbeat_seconds: int = Field(
        default=60,
        ge=1,
        le=60 * 60,
    )
    background_worker_database_backoff_seconds: int = Field(
        default=10,
        ge=1,
        le=300,
    )
    background_worker_recovery_interval_seconds: int = Field(
        default=60,
        ge=1,
        le=60 * 60,
    )
    background_worker_recovery_retry_delay_seconds: int = Field(
        default=30,
        ge=0,
        le=60 * 60,
    )
    background_worker_recovery_batch_size: int = Field(
        default=100,
        ge=1,
        le=10_000,
    )
    background_worker_id: str | None = None

    provider_credential_keys: SecretStr | None = None
    provider_credential_current_key_version: int = Field(
        default=1,
        ge=1,
    )

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

    @model_validator(mode="after")
    def validate_background_worker_lease_timing(
        self,
    ) -> Settings:
        if self.background_worker_heartbeat_seconds >= self.background_worker_lease_seconds:
            raise ValueError(
                "BACKGROUND_WORKER_HEARTBEAT_SECONDS "
                "must be shorter than "
                "BACKGROUND_WORKER_LEASE_SECONDS."
            )

        return self

    @field_validator("background_worker_id")
    @classmethod
    def normalize_background_worker_id(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized = value.strip()

        if not normalized:
            return None

        if len(normalized) > 255:
            raise ValueError("BACKGROUND_WORKER_ID cannot exceed 255 characters.")

        return normalized

    @model_validator(mode="after")
    def validate_provider_credential_keyring(
        self,
    ) -> Settings:
        """Validate the versioned provider credential keyring."""

        from app.providers.credential_keyring import (
            ProviderCredentialKeyringConfigurationError,
            build_provider_credential_keyring,
        )

        try:
            build_provider_credential_keyring(
                serialized_keys=self.provider_credential_keys,
                current_version=(self.provider_credential_current_key_version),
            )
        except ProviderCredentialKeyringConfigurationError as exc:
            raise ValueError(str(exc)) from exc

        return self

    def provider_credential_keyring(self):
        """Return validated provider credential key configuration."""

        from app.providers.credential_keyring import (
            build_provider_credential_keyring,
        )

        return build_provider_credential_keyring(
            serialized_keys=self.provider_credential_keys,
            current_version=(self.provider_credential_current_key_version),
        )

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
    def forwarded_allow_ip_list(
        self,
    ) -> tuple[str, ...]:
        return tuple(
            value.strip() for value in self.forwarded_allow_ips.split(",") if value.strip()
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

        forwarded_allow_ips = self.forwarded_allow_ip_list

        if not forwarded_allow_ips:
            raise ValueError("FORWARDED_ALLOW_IPS cannot be empty.")

        if frontend_origin not in origins:
            raise ValueError("FRONTEND_ORIGIN must also appear in TRUSTED_ORIGINS.")

        for origin in origins:
            parsed = urlparse(origin)

            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Invalid trusted origin: {origin}")

        if not self.is_production:
            return self

        if "*" in forwarded_allow_ips:
            raise ValueError("Production cannot trust forwarded headers from every source.")

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
