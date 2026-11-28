"""Redeem password-reset tokens for personal accounts and legacy administrators.

Reset tokens are created only by the transactional-email worker (ADR 0009).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import can_log_in_by_email
from app.auth.security import hash_password
from app.db.auth_models import SessionRevocationReason, User
from app.repositories.account_token_repository import (
    AccountTokenRepository,
)
from app.repositories.session_repository import SessionRepository
from app.services.identity_email import (
    cancel_password_reset_deliveries,
    lock_open_password_reset_deliveries,
    queue_password_changed,
)


class PasswordRecoveryError(ValueError):
    """The password-reset token cannot be accepted."""


class PasswordRecoveryStateError(RuntimeError):
    """The associated account state is invalid."""


@dataclass(frozen=True, slots=True)
class CompletedPasswordReset:
    """Safe result of a completed password reset."""

    user: User
    revoked_sessions: int


def reset_password(
    session: Session,
    *,
    raw_token: str,
    new_password: str,
    now: datetime | None = None,
) -> CompletedPasswordReset:
    """Redeem one reset token, replace the password, and revoke every session.

    In the same transaction every other open (pending, sending, or
    retry-scheduled) reset delivery for the user is canceled with
    its queued job, every unused reset token is invalidated, and the
    tokenless password-changed notice is queued. Any failure rolls back the
    whole change.
    """

    current_time = now or datetime.now(UTC)
    repository = AccountTokenRepository(session)

    # Lock order: administrator (FOR NO KEY UPDATE), then every open reset
    # delivery, then the reset token. The worker locks a delivery before it
    # inserts a token (a key-share reference to the user) or supersedes
    # tokens, so this order cannot deadlock with it, while FOR NO KEY UPDATE
    # still serializes with reset requests that lock the administrator.
    owner_id = repository.find_password_reset_token_user_id(raw_token)

    if owner_id is None:
        raise PasswordRecoveryError("The password reset link is invalid or expired.")

    user = session.scalar(select(User).where(User.id == owner_id).with_for_update(key_share=True))

    if user is None:
        raise PasswordRecoveryError("The password reset link is invalid or expired.")

    open_resets = lock_open_password_reset_deliveries(
        session, workspace_id=user.workspace_id, user_id=user.id
    )

    token = repository.redeem_password_reset_token(
        raw_token,
        now=current_time,
    )

    if token is None or token.user_id != user.id:
        raise PasswordRecoveryError("The password reset link is invalid or expired.")

    if not can_log_in_by_email(user) or not user.is_active:
        raise PasswordRecoveryStateError("The password-reset user cannot log in by email.")

    if user.email_verified_at is None:
        raise PasswordRecoveryStateError("The account email is not verified.")

    user.password_hash = hash_password(new_password)

    # Runs after the delivery locks, so it also covers any token a racing
    # worker committed for one of those deliveries before we acquired them.
    repository.invalidate_password_reset_tokens(
        user_id=user.id,
        now=current_time,
    )
    revoked_sessions = SessionRepository(session).revoke_all_user_sessions(
        user_id=user.id,
        reason=SessionRevocationReason.PASSWORD_RESET,
        now=current_time,
    )
    # A still-open reset delivery must never mint a token after the change.
    cancel_password_reset_deliveries(session, open_resets, now=current_time)
    queue_password_changed(
        session,
        user_id=user.id,
        reset_token_id=token.id,
        workspace_id=user.workspace_id,
    )

    session.flush()

    return CompletedPasswordReset(
        user=user,
        revoked_sessions=revoked_sessions,
    )
