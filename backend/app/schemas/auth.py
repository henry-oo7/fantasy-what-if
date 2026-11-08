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
