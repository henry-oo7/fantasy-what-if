"""Public SaaS registration schemas."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class AdministratorRegistrationRequest(BaseModel):
    """Public fields required for provisional registration."""

    workspace_name: str = Field(
        min_length=1,
        max_length=120,
    )
    workspace_slug: str = Field(
        min_length=3,
        max_length=50,
    )
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


class AdministratorRegistrationResponse(BaseModel):
    """Safe result returned after registration and email delivery."""

    workspace_slug: str
    registration_status: Literal["provisional"]
    onboarding_stage: Literal["email_verification_pending"]
    verification_required: Literal[True] = True


class EmailVerificationRequest(BaseModel):
    """One plaintext verification token received from the email link."""

    token: str = Field(
        min_length=1,
        max_length=512,
    )


class EmailVerificationResponse(BaseModel):
    """Safe authenticated onboarding state after verification."""

    workspace_slug: str
    display_name: str
    email: str
    role: Literal["admin"]
    registration_status: Literal["provisional"]
    onboarding_stage: Literal["provider_setup_pending"]
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
