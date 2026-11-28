from pydantic import (
    BaseModel,
    EmailStr,
    Field,
)

from app.db.auth_models import AuthSessionPurpose, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.services.account_navigation import AccountNavigationState
from app.services.workspace_lifecycle import WorkspaceAccess


class WorkspaceLifecycleResponse(BaseModel):
    """A legacy workspace user's own workspace lifecycle (compatibility).

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


class LegacyWorkspaceContext(BaseModel):
    """Legacy workspace compatibility, present only for a legacy workspace user.

    Removed with the workspace schema. A personal account never has one.
    """

    role: UserRole
    workspace: WorkspaceLifecycleResponse


class CurrentUserResponse(BaseModel):
    """GET /api/auth/me: the personal account and its navigation state.

    No username, internal ID, role authority, credential, connection,
    access row, or another user's data is ever returned.
    """

    authenticated: bool = True
    display_name: str
    email: EmailStr | None
    email_verified: bool
    navigation_state: AccountNavigationState
    legacy_workspace: LegacyWorkspaceContext | None = None
    # deletion_recovery sessions (legacy) may use only /auth/me, logout, and
    # deletion status and cancellation.
    session_purpose: AuthSessionPurpose = AuthSessionPurpose.STANDARD


class CsrfTokenResponse(BaseModel):
    csrf_token: str


class LogoutResponse(BaseModel):
    logged_out: bool


class LoginRequest(BaseModel):
    """Email and password: the sole login credentials."""

    email: EmailStr
    password: str = Field(
        min_length=1,
        max_length=128,
    )


class AuthenticatedUserResponse(BaseModel):
    """The signed-in account. Legacy fields are omitted for personal accounts."""

    display_name: str
    email: EmailStr
    email_verified: bool
    is_active: bool
    # Legacy workspace compatibility only; excluded when None.
    workspace_id: int | None = None
    role: str | None = None


class AuthenticationResponse(BaseModel):
    """A session created through email login."""

    user: AuthenticatedUserResponse
    csrf_token: str
    session_purpose: AuthSessionPurpose = AuthSessionPurpose.STANDARD


class PasswordResetRequest(BaseModel):
    """Enumeration-safe password-reset request."""

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
    """Safe result requiring a new login."""

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
