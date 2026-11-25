"""Per-user League Season access and personal preferences (ADR 0010).

Schema foundation only. No route or service reads these tables as
authorization yet; the workspace model remains the active authority.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin
from app.db.platform_enums import UserSeasonAccessStatus, UserSeasonProofMode


class UserPreference(TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Application code must verify the user still has access to this Season.
    default_league_season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class UserLeagueSeasonAccess(TimestampMixin, Base):
    """One user's independently proven access to one canonical League Season.

    ``provider_connection_id`` is RESTRICT: a connection that backs access
    cannot be deleted until access is explicitly transitioned or removed, so
    the relationship is never silently erased. Ownership of the connection by
    the same user is an application invariant, not a database trigger.
    """

    __tablename__ = "user_league_season_access"
    __table_args__ = (
        UniqueConstraint("user_id", "season_id", name="uq_user_league_season_access_user_season"),
        CheckConstraint(
            "(proof_mode = 'public' AND provider_connection_id IS NULL) "
            "OR (proof_mode = 'private_credentials' AND provider_connection_id IS NOT NULL)",
            name="ck_user_league_season_access_proof_connection",
        ),
        Index(
            "ix_user_league_season_access_active_season",
            "season_id",
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_user_league_season_access_connection",
            "provider_connection_id",
            postgresql_where=text("provider_connection_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="RESTRICT"),
        nullable=True,
    )
    proof_mode: Mapped[UserSeasonProofMode] = mapped_column(
        Enum(
            UserSeasonProofMode,
            name="user_season_proof_mode",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )
    status: Mapped[UserSeasonAccessStatus] = mapped_column(
        Enum(
            UserSeasonAccessStatus,
            name="user_season_access_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=UserSeasonAccessStatus.ACTIVE,
        server_default=UserSeasonAccessStatus.ACTIVE.value,
    )
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reverified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
