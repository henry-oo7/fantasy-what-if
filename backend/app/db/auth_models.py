from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin


class UserRole(StrEnum):
    SPECTATOR = "spectator"
    ADMIN = "admin"


class SessionRevocationReason(StrEnum):
    """Why one authentication session was revoked.

    ``replaced`` is the only reason disclosed to the client, as the
    ``session_replaced`` error code. ``workspace_deletion_requested``
    revokes every workspace session when FR-20 deletion is requested.
    """

    REPLACED = "replaced"
    LOGOUT = "logout"
    PASSWORD_RESET = "password_reset"
    SPECTATOR_PASSWORD_RESET = "spectator_password_reset"
    SPECTATOR_DISABLED = "spectator_disabled"
    WORKSPACE_DELETION_REQUESTED = "workspace_deletion_requested"


class AuthSessionPurpose(StrEnum):
    """What one authentication session is authorized to do."""

    STANDARD = "standard"
    DELETION_RECOVERY = "deletion_recovery"


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index(
            "uq_users_one_admin_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("workspace_id IS NOT NULL AND role = 'admin'::user_role"),
        ),
        Index(
            "uq_users_one_spectator_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("workspace_id IS NOT NULL AND role = 'spectator'::user_role"),
        ),
        # Global login identity: one normalized email maps to one user.
        Index(
            "uq_users_normalized_email",
            "normalized_email",
            unique=True,
            postgresql_where=text("normalized_email IS NOT NULL"),
        ),
        # A row is either legacy (role and username present) or a
        # workspace-free personal account (neither; one email).
        CheckConstraint(
            "(role IS NOT NULL AND username IS NOT NULL) OR "
            "(workspace_id IS NULL AND role IS NULL AND username IS NULL "
            "AND normalized_email IS NOT NULL AND email = normalized_email "
            "AND btrim(display_name) <> '')",
            name="ck_users_identity_shape",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # NULL for personal accounts; legacy rows keep an internal value.
    username: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        unique=True,
        index=True,
    )

    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "installations.id",
            name="fk_users_workspace_id_installations",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        unique=True,
        index=True,
    )

    normalized_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )

    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    password_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # NULL for personal accounts; legacy workspace rows keep admin/spectator.
    role: Mapped[UserRole | None] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


# Delivery-issued tokens record both the delivery and the attempt; IS [NOT]
# NULL never evaluates to SQL NULL, so a partial pair cannot pass.
_DELIVERY_ATTEMPT_PAIR = (
    "(transactional_email_delivery_id IS NULL AND delivery_attempt IS NULL) "
    "OR (transactional_email_delivery_id IS NOT NULL AND delivery_attempt IS NOT NULL)"
)


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"
    __table_args__ = (
        UniqueConstraint(
            "token_hash",
            name="uq_email_verification_tokens_token_hash",
        ),
        CheckConstraint(
            _DELIVERY_ATTEMPT_PAIR,
            name="ck_email_verification_tokens_delivery_attempt_pair",
        ),
        CheckConstraint(
            "delivery_attempt IS NULL OR delivery_attempt > 0",
            name="ck_email_verification_tokens_delivery_attempt_positive",
        ),
        Index(
            "ix_email_verification_tokens_user_delivery",
            "user_id",
            "transactional_email_delivery_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_email_verification_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Set only by the delivery worker; NULL for synchronous-route tokens.
    transactional_email_delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "transactional_email_deliveries.id",
            name="fk_email_verification_tokens_transactional_email_delivery_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    delivery_attempt: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        UniqueConstraint(
            "token_hash",
            name="uq_password_reset_tokens_token_hash",
        ),
        CheckConstraint(
            _DELIVERY_ATTEMPT_PAIR,
            name="ck_password_reset_tokens_delivery_attempt_pair",
        ),
        CheckConstraint(
            "delivery_attempt IS NULL OR delivery_attempt > 0",
            name="ck_password_reset_tokens_delivery_attempt_positive",
        ),
        Index(
            "ix_password_reset_tokens_user_delivery",
            "user_id",
            "transactional_email_delivery_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_password_reset_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Set only by the delivery worker; NULL for synchronous-route tokens.
    transactional_email_delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "transactional_email_deliveries.id",
            name="fk_password_reset_tokens_transactional_email_delivery_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    delivery_attempt: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index(
            "ix_auth_sessions_user_expiry",
            "user_id",
            "expires_at",
        ),
        CheckConstraint(
            "revocation_reason IS NULL OR revoked_at IS NOT NULL",
            name="ck_auth_sessions_revocation_reason_requires_revoked_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    session_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    csrf_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    revocation_reason: Mapped[SessionRevocationReason | None] = mapped_column(
        Enum(
            SessionRevocationReason,
            name="session_revocation_reason",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
    )

    purpose: Mapped[AuthSessionPurpose] = mapped_column(
        Enum(
            AuthSessionPurpose,
            name="auth_session_purpose",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=AuthSessionPurpose.STANDARD,
        server_default=AuthSessionPurpose.STANDARD.value,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )


class LoginAttempt(Base):
    """Persistent, privacy-preserving login throttle state."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        UniqueConstraint(
            "username_hash",
            "source_hash",
            name="uq_login_attempts_username_source",
        ),
        Index(
            "ix_login_attempts_locked_until",
            "locked_until",
        ),
        Index(
            "ix_login_attempts_last_failed_at",
            "last_failed_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    username_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    source_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    failed_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    first_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RegistrationRateLimit(Base):
    """Privacy-preserving limit state for one public action."""

    __tablename__ = "registration_rate_limits"
    __table_args__ = (
        UniqueConstraint(
            "action",
            "identity_hash",
            name=("uq_registration_rate_limits_action_identity"),
        ),
        Index(
            "ix_registration_rate_limits_last_request_at",
            "last_request_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    identity_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    request_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_request_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
