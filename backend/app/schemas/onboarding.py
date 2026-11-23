"""Typed provider-validation boundary for provisional SaaS onboarding."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.db.platform_enums import OnboardingStage, ProviderAccessMode

MINIMUM_SEASON_YEAR = 2000


class EspnPrivateCredentials(BaseModel):
    """Private ESPN league cookies; both are required together."""

    model_config = ConfigDict(extra="forbid")

    swid: SecretStr = Field(max_length=128)
    espn_s2: SecretStr = Field(max_length=1024)

    @field_validator("swid", "espn_s2")
    @classmethod
    def reject_blank(cls, value: SecretStr) -> SecretStr:
        stripped = value.get_secret_value().strip()

        if not stripped:
            raise ValueError("Credential fields must not be blank.")

        return SecretStr(stripped)


class ProviderValidationRequest(BaseModel):
    """League target plus optional private credentials.

    Workspace ownership is never accepted from the body; unknown fields
    such as workspace_id are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    league_id: str = Field(min_length=1, max_length=15, pattern=r"^[0-9]+$")
    season_year: int = Field(ge=MINIMUM_SEASON_YEAR, le=2100)
    credentials: EspnPrivateCredentials | None = None

    @field_validator("league_id")
    @classmethod
    def normalize_league_id(cls, value: str) -> str:
        normalized = value.lstrip("0")

        if not normalized:
            raise ValueError("League ID must be a positive number.")

        return normalized

    @field_validator("season_year")
    @classmethod
    def reject_future_season(cls, value: int) -> int:
        if value > datetime.now(UTC).year:
            raise ValueError("Season year cannot be in the future.")

        return value


class ProviderValidationResponse(BaseModel):
    """Safe outcome. Never echoes credentials or the submitted target."""

    status: Literal["validated", "credentials_required"]
    code: Literal["provider_validated", "credentials_required"]
    provider: Literal["espn"]
    access_mode: ProviderAccessMode | None
    onboarding_stage: OnboardingStage


class ProviderTargetResponse(BaseModel):
    """Safe validated target. Never includes credentials or key versions."""

    provider: Literal["espn"]
    league_id: str
    season_year: int
    access_mode: ProviderAccessMode


class ProviderClaimRequest(BaseModel):
    """Empty confirmation body. Ownership is never accepted from the browser."""

    model_config = ConfigDict(extra="forbid")


class ProviderClaimResponse(BaseModel):
    status: Literal["claimed"]
    code: Literal["provider_league_claimed"]
    onboarding_stage: Literal["initial_sync_pending"]
    target: ProviderTargetResponse
