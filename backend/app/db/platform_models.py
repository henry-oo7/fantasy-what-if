"""Canonical installation and provider connection models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.db.base import Base
from app.db.mixins import TimestampMixin
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    InstallationStatus,
)

if TYPE_CHECKING:
    from app.db.models import League, Season, SyncRun


class Installation(TimestampMixin, Base):
    __tablename__ = "installations"
    __table_args__ = (
        Index(
            "uq_installations_normalized_slug",
            "normalized_slug",
            unique=True,
            postgresql_where=text("normalized_slug IS NOT NULL"),
        ),
    )

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

    slug: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    normalized_slug: Mapped[str | None] = mapped_column(
        String(50),
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

    active_league_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "leagues.id",
            name=("fk_installations_active_league_id_leagues"),
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )

    active_season_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "seasons.id",
            name=("fk_installations_active_season_id_seasons"),
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )

    provider_connections: Mapped[list[ProviderConnection]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
    )

    leagues: Mapped[list[League]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
        foreign_keys="League.installation_id",
    )

    active_league: Mapped[League | None] = relationship(
        foreign_keys=[active_league_id],
        post_update=True,
    )

    active_season: Mapped[Season | None] = relationship(
        foreign_keys=[active_season_id],
        post_update=True,
    )


class ProviderConnection(TimestampMixin, Base):
    __tablename__ = "provider_connections"
    __table_args__ = (
        CheckConstraint(
            "credential_schema_version > 0",
            name="ck_provider_connections_credential_schema_version_positive",
        ),
    )

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

    credential_schema_version: Mapped[int] = mapped_column(
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

    sync_runs: Mapped[list[SyncRun]] = relationship(
        back_populates="provider_connection",
    )


class ProviderLeagueClaim(TimestampMixin, Base):
    """Exclusive ownership of one provider league identity."""

    __tablename__ = "provider_league_claims"
    __table_args__ = (
        CheckConstraint(
            "provider_league_id = btrim(provider_league_id)",
            name="ck_provider_league_claims_trimmed_id",
        ),
        CheckConstraint(
            "normalized_provider_league_id = btrim(provider_league_id)",
            name="ck_provider_league_claims_normalized_id",
        ),
        UniqueConstraint(
            "workspace_id",
            name="uq_provider_league_claims_workspace_id",
        ),
        UniqueConstraint(
            "league_id",
            name="uq_provider_league_claims_league_id",
        ),
        UniqueConstraint(
            "provider",
            "normalized_provider_league_id",
            name="uq_provider_league_claims_provider_identity",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey(
            "installations.id",
            name=("fk_provider_league_claims_workspace_id_installations"),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    league_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "leagues.id",
            name=("fk_provider_league_claims_league_id_leagues"),
            ondelete="CASCADE",
        ),
        nullable=True,
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

    provider_league_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    normalized_provider_league_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )


class BackgroundJob(TimestampMixin, Base):
    """Durable workspace-owned background work."""

    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint(
            "payload_version > 0",
            name=("ck_background_jobs_payload_version_positive"),
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name=("ck_background_jobs_attempt_count_nonnegative"),
        ),
        CheckConstraint(
            "maximum_attempts > 0",
            name=("ck_background_jobs_maximum_attempts_positive"),
        ),
        CheckConstraint(
            "attempt_count <= maximum_attempts",
            name="ck_background_jobs_attempt_limit",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR idempotency_key = btrim(idempotency_key)",
            name=("ck_background_jobs_idempotency_key_trimmed"),
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR idempotency_key <> ''",
            name=("ck_background_jobs_idempotency_key_nonempty"),
        ),
        CheckConstraint(
            "status <> 'running'::background_job_status "
            "OR ("
            "claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND claimed_by IS NOT NULL "
            "AND started_at IS NOT NULL"
            ")",
            name="ck_background_jobs_running_claim",
        ),
        CheckConstraint(
            "status <> 'succeeded'::background_job_status OR completed_at IS NOT NULL",
            name=("ck_background_jobs_succeeded_timestamp"),
        ),
        CheckConstraint(
            "status <> 'failed'::background_job_status OR failed_at IS NOT NULL",
            name="ck_background_jobs_failed_timestamp",
        ),
        CheckConstraint(
            "status <> 'canceled'::background_job_status OR canceled_at IS NOT NULL",
            name="ck_background_jobs_canceled_timestamp",
        ),
        Index(
            "ix_background_jobs_queue_order",
            "status",
            text("priority DESC"),
            "available_at",
            "id",
        ),
        Index(
            "uq_background_jobs_active_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text(
                "idempotency_key IS NOT NULL "
                "AND status IN ("
                "'pending'::background_job_status, "
                "'running'::background_job_status, "
                "'retry_scheduled'::background_job_status"
                ")"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey(
            "installations.id",
            name=("fk_background_jobs_workspace_id_installations"),
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    job_type: Mapped[BackgroundJobType] = mapped_column(
        Enum(
            BackgroundJobType,
            name="background_job_type",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )

    status: Mapped[BackgroundJobStatus] = mapped_column(
        Enum(
            BackgroundJobStatus,
            name="background_job_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=BackgroundJobStatus.PENDING,
        server_default=BackgroundJobStatus.PENDING.value,
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    payload_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    progress: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    maximum_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        server_default="5",
    )

    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    claimed_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    safe_error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    safe_error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    retryable: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
