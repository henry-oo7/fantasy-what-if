from datetime import datetime

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


class SyncResultResponse(BaseModel):
    provider: str
    season_id: int
    provider_league_id: str
    provider_season_id: str
    current_week: int
    full_sync: bool
    player_weeks: tuple[int, ...]
    teams_upserted: int
    matchups_upserted: int
    fantasy_byes_upserted: int
    player_week_scores_upserted: int


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
