"""Public SaaS registration schemas."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.services.account_navigation import AccountNavigationState


class RegistrationRequest(BaseModel):
    """Public fields required to create a personal account."""

    display_name: str = Field(
        min_length=1,
        max_length=100,
    )
    email: EmailStr = Field(
        max_length=320,
    )
    password: str = Field(
        min_length=12,
        max_length=128,
    )
    confirm_password: str = Field(
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def passwords_match(self) -> RegistrationRequest:
        if self.password != self.confirm_password:
            raise ValueError("The passwords do not match.")

        return self


class RegistrationResponse(BaseModel):
    """Generic result: verification was queued, never that mail was delivered."""

    verification_required: Literal[True] = True


class EmailVerificationRequest(BaseModel):
    """One plaintext verification token received from the email link."""

    token: str = Field(
        min_length=1,
        max_length=512,
    )


class EmailVerificationResponse(BaseModel):
    """Safe authenticated state after verification."""

    display_name: str
    email: str
    email_verified: Literal[True] = True
    navigation_state: AccountNavigationState
    csrf_token: str


class VerificationResendRequest(BaseModel):
    """Enumeration-safe verification resend request."""

    email: EmailStr = Field(
        max_length=320,
    )


class VerificationResendResponse(BaseModel):
    """Generic response that does not disclose account existence."""

    detail: str
    code: Literal["verification_resend_accepted"]
