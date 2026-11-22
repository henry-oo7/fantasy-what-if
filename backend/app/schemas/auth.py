from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
)


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
