from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)


class InviteValidationResponse(BaseModel):
    valid: bool
    team_id: int
    team_name: str
    season: int
    intended_display_name: str | None
    expires_at: str


class RegistrationRequest(BaseModel):
    invitation_token: str = Field(
        min_length=20,
        max_length=500,
    )
    username: str = Field(
        min_length=3,
        max_length=50,
    )
    display_name: str = Field(
        min_length=1,
        max_length=100,
    )
    email: EmailStr | None = None
    password: str = Field(
        min_length=12,
        max_length=128,
    )

    @field_validator(
        "invitation_token",
        "username",
        "display_name",
        "password",
    )
    @classmethod
    def reject_blank_strings(
        cls,
        value: str,
    ) -> str:
        if not value.strip():
            raise ValueError("Value cannot be blank.")

        return value


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
    team_id: int | None = None
    team_name: str | None = None
    season: int | None = None


class AuthenticationResponse(BaseModel):
    user: AuthenticatedUserResponse
    csrf_token: str


class CsrfTokenResponse(BaseModel):
    csrf_token: str


class LogoutResponse(BaseModel):
    logged_out: bool
