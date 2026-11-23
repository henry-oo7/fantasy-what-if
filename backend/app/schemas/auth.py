from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
)

from app.db.auth_models import UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.services.workspace_lifecycle import WorkspaceAccess


class LoginRequest(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=50,
    )
    password: str = Field(
        min_length=1,
        max_length=128,
    )


class AuthenticatedUserResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    username: str
    display_name: str
    email: EmailStr | None
    role: str
    is_active: bool


class WorkspaceLifecycleResponse(BaseModel):
    """The authenticated user's own workspace lifecycle.

    workspace_access is authoritative for routing. Lifecycle detail is
    null whenever access is inactive, so unusable or inconsistent states
    are not distinguishable to the client, and onboarding_stage is
    returned only to administrators.
    """

    workspace_id: int
    workspace_slug: str | None
    installation_status: InstallationStatus | None
    registration_status: WorkspaceRegistrationStatus | None
    onboarding_stage: OnboardingStage | None
    workspace_access: WorkspaceAccess


class CurrentUserResponse(BaseModel):
    """GET /api/auth/me: identity plus authoritative workspace lifecycle.

    The internal compatibility username is intentionally omitted.
    """

    id: int
    display_name: str
    email: EmailStr | None
    role: UserRole
    is_active: bool
    workspace: WorkspaceLifecycleResponse


class AuthenticationResponse(BaseModel):
    user: AuthenticatedUserResponse
    csrf_token: str


class CsrfTokenResponse(BaseModel):
    csrf_token: str


class LogoutResponse(BaseModel):
    logged_out: bool


class AdministratorLoginRequest(BaseModel):
    """Administrator email and password credentials."""

    email: EmailStr
    password: str = Field(
        min_length=1,
        max_length=128,
    )


class AdministratorAuthenticatedUserResponse(BaseModel):
    """Public administrator identity without compatibility username."""

    id: int
    workspace_id: int
    display_name: str
    email: EmailStr
    role: str
    is_active: bool


class AdministratorAuthenticationResponse(BaseModel):
    """Administrator session created through email login."""

    user: AdministratorAuthenticatedUserResponse
    csrf_token: str


class PasswordResetRequest(BaseModel):
    """Enumeration-safe administrator password-reset request."""

    email: EmailStr


class PasswordResetRequestResponse(BaseModel):
    """Generic reset response that hides account eligibility."""

    detail: str
    code: str


class PasswordResetCompletionRequest(BaseModel):
    """Single-use reset token and replacement password."""

    token: str = Field(
        min_length=1,
        max_length=512,
    )
    new_password: str = Field(
        min_length=12,
        max_length=128,
    )


class PasswordResetCompletionResponse(BaseModel):
    """Safe result requiring a new administrator login."""

    detail: str
    code: str
    revoked_sessions: int


class SpectatorLoginRequest(BaseModel):
    """Workspace slug and shared spectator password."""

    workspace_slug: str = Field(
        min_length=3,
        max_length=50,
    )
    password: str = Field(
        min_length=1,
        max_length=128,
    )


class SpectatorAuthenticatedUserResponse(BaseModel):
    """Safe shared spectator identity."""

    id: int
    workspace_id: int
    workspace_slug: str
    display_name: str
    role: str
    is_active: bool


class SpectatorAuthenticationResponse(BaseModel):
    """Additional concurrent spectator session."""

    user: SpectatorAuthenticatedUserResponse
    csrf_token: str
