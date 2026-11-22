"""Issue and redeem administrator password-reset tokens."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import (
    generate_account_token,
    hash_password,
)
from app.db.auth_models import User, UserRole
from app.repositories.account_token_repository import (
    AccountTokenRepository,
)
from app.repositories.session_repository import SessionRepository


class PasswordRecoveryError(ValueError):
    """The password-reset token cannot be accepted."""


class PasswordRecoveryStateError(RuntimeError):
    """The associated administrator state is invalid."""


@dataclass(frozen=True, slots=True)
class IssuedPasswordReset:
    """Plaintext delivery value plus its persisted expiration."""

    raw_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CompletedPasswordReset:
    """Safe result of a completed administrator password reset."""

    user: User
    revoked_sessions: int


def issue_password_reset_token(
    session: Session,
    *,
    user: User,
    lifetime_minutes: int,
    now: datetime | None = None,
) -> IssuedPasswordReset:
    """Replace prior reset tokens and return one delivery token."""

    if user.role is not UserRole.ADMIN:
        raise PasswordRecoveryStateError("Password recovery requires an administrator.")

    if user.workspace_id is None:
        raise PasswordRecoveryStateError("The administrator has no workspace.")

    if user.email_verified_at is None:
        raise PasswordRecoveryStateError("The administrator email is not verified.")

    current_time = now or datetime.now(UTC)
    expires_at = current_time + timedelta(minutes=lifetime_minutes)
    raw_token = generate_account_token()

    AccountTokenRepository(session).create_password_reset_token(
        user_id=user.id,
        raw_token=raw_token,
        expires_at=expires_at,
        now=current_time,
    )

    return IssuedPasswordReset(
        raw_token=raw_token,
        expires_at=expires_at,
    )


def reset_administrator_password(
    session: Session,
    *,
    raw_token: str,
    new_password: str,
    now: datetime | None = None,
) -> CompletedPasswordReset:
    """Redeem one reset token, replace the password, and revoke sessions."""

    current_time = now or datetime.now(UTC)
    repository = AccountTokenRepository(session)
    token = repository.redeem_password_reset_token(
        raw_token,
        now=current_time,
    )

    if token is None:
        raise PasswordRecoveryError("The password reset link is invalid or expired.")

    user = session.scalar(select(User).where(User.id == token.user_id).with_for_update())

    if user is None:
        raise PasswordRecoveryStateError("The password-reset user no longer exists.")

    if user.role is not UserRole.ADMIN:
        raise PasswordRecoveryStateError("The password-reset user is not an administrator.")

    if user.workspace_id is None:
        raise PasswordRecoveryStateError("The password-reset user has no workspace.")

    if user.email_verified_at is None:
        raise PasswordRecoveryStateError("The administrator email is not verified.")

    user.password_hash = hash_password(new_password)

    repository.invalidate_password_reset_tokens(
        user_id=user.id,
        now=current_time,
    )
    revoked_sessions = SessionRepository(session).revoke_all_user_sessions(
        user_id=user.id,
        now=current_time,
    )

    session.flush()

    return CompletedPasswordReset(
        user=user,
        revoked_sessions=revoked_sessions,
    )
