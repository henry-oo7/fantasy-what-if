"""Canonical installation and provider connection models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.db.base import Base
from app.db.mixins import TimestampMixin
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
)

if TYPE_CHECKING:
    from app.db.models import League, Season


class Installation(TimestampMixin, Base):
    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    application_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    public_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    timezone: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="America/Chicago",
        server_default="America/Chicago",
    )

    status: Mapped[InstallationStatus] = mapped_column(
        Enum(
            InstallationStatus,
            name="installation_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=InstallationStatus.SETUP_REQUIRED,
        server_default=(InstallationStatus.SETUP_REQUIRED.value),
    )

    setup_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    setup_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    provider_connections: Mapped[list[ProviderConnection]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
    )

    leagues: Mapped[list[League]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
    )


class ProviderConnection(TimestampMixin, Base):
    __tablename__ = "provider_connections"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    installation_id: Mapped[int] = mapped_column(
        ForeignKey(
            "installations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    provider: Mapped[FantasyProvider] = mapped_column(
        Enum(
            FantasyProvider,
            name="fantasy_provider",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        index=True,
    )

    display_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    encrypted_credentials: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    credential_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
    )

    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_validation_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    installation: Mapped[Installation] = relationship(
        back_populates="provider_connections",
    )

    seasons: Mapped[list[Season]] = relationship(
        back_populates="provider_connection",
    )
