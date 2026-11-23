from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class SyncRunResponse(BaseModel):
    id: int
    status: str
    is_full_sync: bool
    started_at: datetime
    completed_at: datetime | None
    requested_from_week: int | None
    requested_through_week: int | None
    teams_upserted: int
    matchups_upserted: int
    player_week_scores_upserted: int
    safe_error_message: str | None


class SyncStatusResponse(BaseModel):
    provider: str
    season_id: int
    provider_league_id: str
    provider_season_id: str
    last_synced_at: datetime | None
    latest_run: SyncRunResponse | None


class SyncRequest(BaseModel):
    full_sync: bool = False


class SyncJobAcceptedResponse(BaseModel):
    job_id: int
    status: str
    existing: bool


class BackgroundJobStatusResponse(BaseModel):
    job_id: int
    job_type: str
    status: str
    attempt_count: int
    maximum_attempts: int
    progress: dict[str, Any] | None
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    canceled_at: datetime | None
    safe_error_code: str | None
    safe_error_message: str | None
    retryable: bool | None


SpectatorPassword = Annotated[str, Field(min_length=14, max_length=128)]


class SpectatorAccountResponse(BaseModel):
    """Safe shared spectator state.

    Never includes a password, hash, internal username, email or sessions.
    """

    workspace_slug: str | None
    configured: bool
    enabled: bool


class CreateSpectatorRequest(BaseModel):
    """Create the workspace's one shared spectator. Only the password is chosen."""

    model_config = ConfigDict(extra="forbid")

    password: SpectatorPassword


class ResetSpectatorPasswordRequest(BaseModel):
    """Replace the shared spectator password."""

    model_config = ConfigDict(extra="forbid")

    password: SpectatorPassword


class SetSpectatorAccessRequest(BaseModel):
    """Disable access, or re-enable it with a new shared password."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool
    password: SpectatorPassword | None = None


class SpectatorAccountMutationResponse(SpectatorAccountResponse):
    """Safe state plus the number of spectator sessions signed out."""

    revoked_sessions: int = 0


class StandingsTiebreakerConfigurationResponse(BaseModel):
    """Standings tiebreaker configuration for the active season."""

    season_id: int
    provider_rule: str | None
    provider_rule_supported: bool
    admin_fallback_rule: str | None
    effective_rule: str | None
    effective_rule_source: str | None


class SetStandingsTiebreakerFallbackRequest(BaseModel):
    """Administrator-selected fallback standings tiebreaker."""

    fallback_rule: str


class ReplaceProviderCredentialsRequest(BaseModel):
    """Replacement secrets for the active provider connection."""

    espn_s2: str
    swid: str


class ReplaceProviderCredentialsResponse(BaseModel):
    """Safe metadata returned after provider credential replacement."""

    workspace_id: int
    connection_id: int
    provider: str
    provider_league_id: str
    provider_season_id: str
    credential_version: int
    credential_schema_version: int
    validated_at: datetime
