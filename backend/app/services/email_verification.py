"""Redeem email-verification tokens for personal accounts and legacy administrators.

Verification tokens are created only by the transactional-email worker
(ADR 0009).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import IdentityKind, classify_identity
from app.db.auth_models import User
from app.db.platform_enums import (
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.repositories.account_token_repository import (
    AccountTokenRepository,
)


class EmailVerificationError(ValueError):
    """The verification token cannot be accepted."""


class EmailVerificationStateError(RuntimeError):
    """The associated registration state is invalid."""


def verify_email(
    session: Session,
    *,
    raw_token: str,
    now: datetime | None = None,
) -> User:
    """Redeem one token. A legacy administrator also advances provisional onboarding."""

    current_time = now or datetime.now(UTC)
    repository = AccountTokenRepository(session)
    token = repository.redeem_email_verification_token(
        raw_token,
        now=current_time,
    )

    if token is None:
        raise EmailVerificationError("The email verification link is invalid or expired.")

    user = session.scalar(select(User).where(User.id == token.user_id).with_for_update())

    if user is None:
        raise EmailVerificationStateError("The verification user no longer exists.")

    identity = classify_identity(user)

    if identity is IdentityKind.LEGACY_ADMINISTRATOR:
        workspace = session.scalar(
            select(Installation).where(Installation.id == user.workspace_id).with_for_update()
        )

        if workspace is None:
            raise EmailVerificationStateError("The verification workspace no longer exists.")

        if workspace.registration_status is not WorkspaceRegistrationStatus.PROVISIONAL:
            raise EmailVerificationStateError("The workspace is not provisional.")

        if workspace.onboarding_stage is not OnboardingStage.EMAIL_VERIFICATION_PENDING:
            raise EmailVerificationStateError("The workspace is not awaiting email verification.")

        workspace.onboarding_stage = OnboardingStage.PROVIDER_SETUP_PENDING
    elif identity is not IdentityKind.PERSONAL:
        raise EmailVerificationStateError("The verification user cannot verify by email.")

    user.email_verified_at = current_time

    repository.invalidate_email_verification_tokens(
        user_id=user.id,
        now=current_time,
    )
    session.flush()

    return user
