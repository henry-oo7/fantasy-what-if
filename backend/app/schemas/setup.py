"""Public setup lifecycle schemas."""

from typing import Literal

from pydantic import BaseModel


class SetupStatusResponse(BaseModel):
    """Minimum public state required for setup routing."""

    status: Literal[
        "setup_required",
        "setup_in_progress",
        "active",
    ]
    installation_exists: bool
    bootstrap_available: bool
    administrator_configured: bool
    provider_configured: bool
    active_league_configured: bool


class BootstrapAdminRequest(BaseModel):
    """Initial administrator account details."""

    username: str
    display_name: str
    email: str | None = None
    password: str


class BootstrapAdminResponse(BaseModel):
    """Public result of the one-time administrator bootstrap."""

    installation_status: Literal["setup_in_progress"]
    administrator_id: int
    username: str
    display_name: str
    email: str | None


class SetupProviderResponse(BaseModel):
    """One provider visible during installation setup."""

    provider: Literal["espn", "sleeper", "yahoo"]
    display_name: str
    implemented: bool
    requires_credentials: bool
    credential_fields: tuple[str, ...]


class SetupProvidersResponse(BaseModel):
    """Providers supported by the platform setup contract."""

    providers: tuple[SetupProviderResponse, ...]


class SetupProviderConnectionRequest(BaseModel):
    """Provider configuration submitted during setup."""

    provider: Literal["espn", "sleeper", "yahoo"]
    display_name: str
    league_id: str
    season: str
    espn_s2: str | None = None
    swid: str | None = None


class SetupProviderConnectionResponse(BaseModel):
    """Safe metadata for a stored provider connection."""

    connection_id: int
    provider: Literal["espn", "sleeper", "yahoo"]
    display_name: str
    credential_version: int
    is_active: bool
    validated: bool


class SetupFinalizationResponse(BaseModel):
    """Safe summary of a completed installation setup."""

    installation_status: Literal["active"]
    installation_id: int
    league_id: int
    season_id: int
    provider: Literal["espn", "sleeper", "yahoo"]
    provider_league_id: str
    provider_season_id: str
    current_week: int
    full_sync: bool
    teams_upserted: int
    matchups_upserted: int
    fantasy_byes_upserted: int
    player_week_scores_upserted: int
