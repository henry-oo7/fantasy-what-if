"""Persist and redeem email-verification and password-reset tokens."""

from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.auth.security import hash_token
from app.db.auth_models import (
    EmailVerificationToken,
    PasswordResetToken,
)

AccountToken = TypeVar(
    "AccountToken",
    EmailVerificationToken,
    PasswordResetToken,
)


class AccountTokenRepository:
    """Manage hashed, expiring, single-use account tokens."""

    def __init__(
        self,
        session: Session,
    ) -> None:
        self.session = session

    def create_email_verification_token(
        self,
        *,
        user_id: int,
        raw_token: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> EmailVerificationToken:
        current_time = now or datetime.now(UTC)

        self._invalidate_unused_tokens(
            EmailVerificationToken,
            user_id=user_id,
            now=current_time,
        )

        token = EmailVerificationToken(
            user_id=user_id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
            created_at=current_time,
        )
        self.session.add(token)
        self.session.flush()

        return token

    def create_password_reset_token(
        self,
        *,
        user_id: int,
        raw_token: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> PasswordResetToken:
        current_time = now or datetime.now(UTC)

        self._invalidate_unused_tokens(
            PasswordResetToken,
            user_id=user_id,
            now=current_time,
        )

        token = PasswordResetToken(
            user_id=user_id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
            created_at=current_time,
        )
        self.session.add(token)
        self.session.flush()

        return token

    def create_delivery_email_verification_token(
        self,
        *,
        user_id: int,
        raw_token: str,
        expires_at: datetime,
        delivery_id: int,
        delivery_attempt: int,
        now: datetime | None = None,
    ) -> EmailVerificationToken:
        """Store a worker-issued verification token hash without invalidation.

        Earlier tokens stay valid: a prior uncertain attempt may have been
        delivered. Supersession happens only on provider acceptance.
        """

        return self._create_delivery_token(
            EmailVerificationToken,
            user_id=user_id,
            raw_token=raw_token,
            expires_at=expires_at,
            delivery_id=delivery_id,
            delivery_attempt=delivery_attempt,
            now=now,
        )

    def create_delivery_password_reset_token(
        self,
        *,
        user_id: int,
        raw_token: str,
        expires_at: datetime,
        delivery_id: int,
        delivery_attempt: int,
        now: datetime | None = None,
    ) -> PasswordResetToken:
        """Store a worker-issued password-reset token hash without invalidation."""

        return self._create_delivery_token(
            PasswordResetToken,
            user_id=user_id,
            raw_token=raw_token,
            expires_at=expires_at,
            delivery_id=delivery_id,
            delivery_attempt=delivery_attempt,
            now=now,
        )

    def supersede_email_verification_tokens(
        self,
        *,
        user_id: int,
        accepted_delivery_id: int,
        now: datetime | None = None,
    ) -> int:
        """Invalidate legacy and older-delivery verification tokens."""

        return self._supersede_tokens(
            EmailVerificationToken,
            user_id=user_id,
            accepted_delivery_id=accepted_delivery_id,
            now=now or datetime.now(UTC),
        )

    def supersede_password_reset_tokens(
        self,
        *,
        user_id: int,
        accepted_delivery_id: int,
        now: datetime | None = None,
    ) -> int:
        """Invalidate legacy and older-delivery password-reset tokens."""

        return self._supersede_tokens(
            PasswordResetToken,
            user_id=user_id,
            accepted_delivery_id=accepted_delivery_id,
            now=now or datetime.now(UTC),
        )

    def redeem_email_verification_token(
        self,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> EmailVerificationToken | None:
        return self._redeem_token(
            EmailVerificationToken,
            raw_token=raw_token,
            now=now,
        )

    def redeem_password_reset_token(
        self,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> PasswordResetToken | None:
        return self._redeem_token(
            PasswordResetToken,
            raw_token=raw_token,
            now=now,
        )

    def invalidate_email_verification_tokens(
        self,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> int:
        return self._invalidate_unused_tokens(
            EmailVerificationToken,
            user_id=user_id,
            now=now or datetime.now(UTC),
        )

    def invalidate_password_reset_tokens(
        self,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> int:
        return self._invalidate_unused_tokens(
            PasswordResetToken,
            user_id=user_id,
            now=now or datetime.now(UTC),
        )

    def delete_expired_or_used_tokens(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        current_time = now or datetime.now(UTC)

        verification_result = self.session.execute(
            delete(EmailVerificationToken).where(
                or_(
                    EmailVerificationToken.expires_at <= current_time,
                    EmailVerificationToken.used_at.is_not(None),
                )
            )
        )
        reset_result = self.session.execute(
            delete(PasswordResetToken).where(
                or_(
                    PasswordResetToken.expires_at <= current_time,
                    PasswordResetToken.used_at.is_not(None),
                )
            )
        )

        return int((verification_result.rowcount or 0) + (reset_result.rowcount or 0))

    def _redeem_token(
        self,
        model: type[AccountToken],
        *,
        raw_token: str,
        now: datetime | None,
    ) -> AccountToken | None:
        try:
            token_hash = hash_token(raw_token)
        except ValueError:
            return None

        current_time = now or datetime.now(UTC)

        token = self.session.scalar(
            select(model).where(model.token_hash == token_hash).with_for_update()
        )

        if token is None:
            return None

        if token.used_at is not None:
            return None

        if token.expires_at <= current_time:
            return None

        token.used_at = current_time
        self.session.flush()

        return token

    def _create_delivery_token(
        self,
        model: type[AccountToken],
        *,
        user_id: int,
        raw_token: str,
        expires_at: datetime,
        delivery_id: int,
        delivery_attempt: int,
        now: datetime | None,
    ) -> AccountToken:
        if delivery_id < 1 or delivery_attempt < 1:
            raise ValueError("delivery_id and delivery_attempt must be positive.")

        token = model(
            user_id=user_id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
            created_at=now or datetime.now(UTC),
            transactional_email_delivery_id=delivery_id,
            delivery_attempt=delivery_attempt,
        )
        self.session.add(token)
        self.session.flush()

        return token

    def _supersede_tokens(
        self,
        model: type[AccountToken],
        *,
        user_id: int,
        accepted_delivery_id: int,
        now: datetime,
    ) -> int:
        # Delivery IDs are the ordering authority. Tokens of the accepted
        # delivery (every attempt) and of newer in-flight deliveries survive.
        result = self.session.execute(
            update(model)
            .where(
                model.user_id == user_id,
                model.used_at.is_(None),
                or_(
                    model.transactional_email_delivery_id.is_(None),
                    model.transactional_email_delivery_id < accepted_delivery_id,
                ),
            )
            .values(used_at=now)
        )

        return int(result.rowcount or 0)

    def _invalidate_unused_tokens(
        self,
        model: type[AccountToken],
        *,
        user_id: int,
        now: datetime,
    ) -> int:
        result = self.session.execute(
            update(model)
            .where(
                model.user_id == user_id,
                model.used_at.is_(None),
            )
            .values(used_at=now)
        )

        return int(result.rowcount or 0)
