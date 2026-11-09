"""Public setup lifecycle schemas."""

from typing import Literal

from pydantic import BaseModel


class SetupStatusResponse(BaseModel):
    """Minimum public state required for frontend routing."""

    status: Literal[
        "setup_required",
        "setup_in_progress",
        "active",
    ]
    installation_exists: bool
    bootstrap_available: bool


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
