from datetime import datetime
from typing import Any

from pydantic import BaseModel


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


class SpectatorAccountResponse(BaseModel):
    """Safe shared spectator account metadata."""

    configured: bool
    id: int | None = None
    username: str | None = None
    display_name: str | None = None
    email: str | None = None
    is_active: bool | None = None


class CreateSpectatorRequest(BaseModel):
    """Create the installation's shared spectator."""

    username: str
    display_name: str
    email: str | None = None
    password: str


class UpdateSpectatorProfileRequest(BaseModel):
    """Update spectator profile metadata."""

    display_name: str
    email: str | None = None


class ResetSpectatorPasswordRequest(BaseModel):
    """Replace the shared spectator password."""

    password: str


class SetSpectatorAccessRequest(BaseModel):
    """Enable or disable shared spectator access."""

    is_active: bool


class SpectatorAccountMutationResponse(SpectatorAccountResponse):
    """Spectator metadata plus revoked-session count."""

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
