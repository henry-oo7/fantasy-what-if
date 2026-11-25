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
    OnboardingStage,
    ProviderAccessMode,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
    WorkspaceDeletionOutcome,
    WorkspaceRegistrationStatus,
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
        # NULL-safe: the CASE never evaluates to NULL, so a row cannot pass
        # the CHECK through an unknown result. A NULL registration_status
        # takes the ELSE branch and must carry no deletion timestamps.
        CheckConstraint(
            "CASE WHEN registration_status = 'deletion_pending'::workspace_registration_status "
            "THEN deletion_requested_at IS NOT NULL "
            "AND deletion_scheduled_for IS NOT NULL "
            "AND deletion_scheduled_for > deletion_requested_at "
            "ELSE deletion_requested_at IS NULL "
            "AND deletion_scheduled_for IS NULL END",
            name="ck_installations_deletion_lifecycle",
        ),
        Index(
            "ix_installations_deletion_scheduled_for_pending",
            "deletion_scheduled_for",
            postgresql_where=text(
                "registration_status = 'deletion_pending'::workspace_registration_status"
            ),
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

    registration_status: Mapped[WorkspaceRegistrationStatus | None] = mapped_column(
        Enum(
            WorkspaceRegistrationStatus,
            name="workspace_registration_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
        default=None,
        index=True,
    )

    onboarding_stage: Mapped[OnboardingStage | None] = mapped_column(
        Enum(
            OnboardingStage,
            name="onboarding_stage",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
        default=None,
        index=True,
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

    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    deletion_scheduled_for: Mapped[datetime | None] = mapped_column(
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
        CheckConstraint(
            "(encrypted_credentials IS NULL) = (credential_version IS NULL)",
            name="ck_provider_connections_credential_pair",
        ),
        CheckConstraint(
            "(access_mode IS NULL AND provider_league_id IS NULL "
            "AND provider_season_year IS NULL) "
            "OR (access_mode IS NOT NULL AND access_mode = 'public' "
            "AND provider_league_id IS NOT NULL AND provider_season_year IS NOT NULL "
            "AND encrypted_credentials IS NULL) "
            "OR (access_mode IS NOT NULL AND access_mode = 'private' "
            "AND provider_league_id IS NOT NULL AND provider_season_year IS NOT NULL "
            "AND encrypted_credentials IS NOT NULL)",
            name="ck_provider_connections_access_mode_state",
        ),
        CheckConstraint(
            "provider_season_year IS NULL OR provider_season_year BETWEEN 2000 AND 2100",
            name="ck_provider_connections_season_year_range",
        ),
        CheckConstraint(
            "installation_id IS NOT NULL OR user_id IS NOT NULL",
            name="ck_provider_connections_owner_boundary",
        ),
        # One current connection per workspace, regardless of provider.
        Index(
            "uq_provider_connections_active_installation",
            "installation_id",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # Legacy workspace owner; NULL for user-owned connections.
    installation_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "installations.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    # Personal owner; NULL for legacy workspace connections.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_provider_connections_user_id_users",
            ondelete="CASCADE",
        ),
        nullable=True,
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

    # Credential key version; present exactly when ciphertext is present.
    credential_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
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

    # Validated SaaS target. NULL for legacy connections.
    provider_league_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    provider_season_year: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    access_mode: Mapped[ProviderAccessMode | None] = mapped_column(
        Enum(
            ProviderAccessMode,
            name="provider_access_mode",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
    )

    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_validation_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    installation: Mapped[Installation | None] = relationship(
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


class WorkspaceDeletionRecord(Base):
    """Anonymized operational record of one completed workspace deletion.

    Deliberately has no foreign key to ``installations`` and stores no
    slug, email, name, user, username, IP, credential, token, job payload,
    or error text. Historical records for a reused provider identity or
    former workspace ID are expected, so neither is unique.
    """

    __tablename__ = "workspace_deletion_records"
    __table_args__ = (
        CheckConstraint(
            "scheduled_for > requested_at",
            name="ck_workspace_deletion_records_scheduled_after_requested",
        ),
        CheckConstraint(
            "completed_at >= scheduled_for",
            name="ck_workspace_deletion_records_completed_after_scheduled",
        ),
        CheckConstraint(
            "retention_expires_at > completed_at",
            name="ck_workspace_deletion_records_retention_after_completed",
        ),
        CheckConstraint(
            "btrim(normalized_provider_league_id) <> ''",
            name="ck_workspace_deletion_records_league_id_not_blank",
        ),
        CheckConstraint(
            "outcome IN ('completed')",
            name="ck_workspace_deletion_records_outcome",
        ),
        Index(
            "ix_workspace_deletion_records_former_workspace_id",
            "former_workspace_id",
        ),
        Index(
            "ix_workspace_deletion_records_retention_expires_at",
            "retention_expires_at",
        ),
        Index(
            "ix_workspace_deletion_records_provider_identity",
            "provider",
            "normalized_provider_league_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    former_workspace_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    provider: Mapped[FantasyProvider] = mapped_column(
        Enum(
            FantasyProvider,
            name="fantasy_provider",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )

    normalized_provider_league_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    outcome: Mapped[WorkspaceDeletionOutcome] = mapped_column(
        Enum(
            WorkspaceDeletionOutcome,
            name="workspace_deletion_outcome",
            native_enum=False,
            create_constraint=False,
            length=32,
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )

    retention_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class BackgroundJob(TimestampMixin, Base):
    """Durable workspace-owned background work.

    ``workspace_id`` is NULL only for unscoped transactional-email jobs and
    League-Season synchronization jobs whose League has no workspace.
    """

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
        # A NULL workspace is allowed only for unscoped transactional-email
        # jobs and League-Season synchronization (ADR 0010). job_type is NOT
        # NULL, so the OR never evaluates to SQL NULL. The historical name is
        # kept so earlier migrations and their rehearsals stay valid.
        CheckConstraint(
            "workspace_id IS NOT NULL OR job_type::text IN "
            "('transactional_email_delivery', 'league_season_sync')",
            name="ck_background_jobs_workspace_required_except_email",
        ),
        # Season-sync payloads carry exactly {"version": 1, "season_id": <positive
        # integer>}; the Season row is authoritative for everything else.
        CheckConstraint(
            "job_type::text <> 'league_season_sync' "
            "OR COALESCE("
            "payload_version = 1 "
            "AND jsonb_typeof(payload) = 'object' "
            "AND (payload - 'version' - 'season_id') = '{}'::jsonb "
            "AND jsonb_typeof(payload -> 'version') = 'number' "
            "AND payload ->> 'version' = '1' "
            "AND jsonb_typeof(payload -> 'season_id') = 'number' "
            "AND payload ->> 'season_id' ~ '^[1-9][0-9]{0,9}$', "
            "false)",
            name="ck_background_jobs_league_season_sync_payload_shape",
        ),
        # The active idempotency key is derived from the payload Season, so the
        # partial unique index allows at most one active job per Season.
        CheckConstraint(
            "job_type::text <> 'league_season_sync' "
            "OR COALESCE(idempotency_key = 'league-season-sync:' || (payload ->> 'season_id'), "
            "false)",
            name="ck_background_jobs_league_season_sync_key",
        ),
        # Email job payloads carry exactly {version, delivery_id}, both numbers.
        # COALESCE keeps every branch NULL-safe.
        CheckConstraint(
            "job_type::text <> 'transactional_email_delivery' "
            "OR COALESCE("
            "jsonb_typeof(payload) = 'object' "
            "AND (payload - 'version' - 'delivery_id') = '{}'::jsonb "
            "AND payload ? 'version' "
            "AND payload ? 'delivery_id' "
            "AND jsonb_typeof(payload -> 'version') = 'number' "
            "AND jsonb_typeof(payload -> 'delivery_id') = 'number', "
            "false)",
            name="ck_background_jobs_transactional_email_payload_shape",
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

    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "installations.id",
            name=("fk_background_jobs_workspace_id_installations"),
            ondelete="CASCADE",
        ),
        nullable=True,
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


_DELIVERY_NONTERMINAL = "status IN ('pending', 'sending', 'retry_scheduled')"


class TransactionalEmailDelivery(TimestampMixin, Base):
    """Durable transactional-email intent and state.

    The row records what must be sent and what happened. BackgroundJob
    remains the only queue and lease authority. No plaintext recipient,
    token, link, subject, or body is ever stored. Scoped rows derive the
    recipient from the active user at send time; the unscoped completion
    row carries an encrypted recipient only until it reaches a final state.
    """

    __tablename__ = "transactional_email_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_transactional_email_deliveries_idempotency_key",
        ),
        CheckConstraint(
            "idempotency_key = btrim(idempotency_key) AND idempotency_key <> ''",
            name="ck_transactional_email_deliveries_idempotency_key_clean",
        ),
        # Every CASE has an ELSE false branch, so an unexpected or NULL scope
        # can never pass through an unknown result.
        CheckConstraint(
            "CASE "
            "WHEN scope = 'workspace' THEN "
            "workspace_id IS NOT NULL "
            "AND encrypted_recipient IS NULL "
            "AND recipient_key_version IS NULL "
            "AND event_type <> 'workspace_deletion_completed' "
            "WHEN scope = 'unscoped' THEN "
            "workspace_id IS NULL "
            "AND user_id IS NULL "
            "AND event_type = 'workspace_deletion_completed' "
            "AND ((" + _DELIVERY_NONTERMINAL + ") "
            "= (encrypted_recipient IS NOT NULL)) "
            "ELSE false END",
            name="ck_transactional_email_deliveries_scope_shape",
        ),
        CheckConstraint(
            "(encrypted_recipient IS NULL) = (recipient_key_version IS NULL)",
            name="ck_transactional_email_deliveries_recipient_pair",
        ),
        CheckConstraint(
            "recipient_key_version IS NULL OR recipient_key_version > 0",
            name="ck_transactional_email_deliveries_key_version_positive",
        ),
        CheckConstraint(
            "CASE WHEN event_type IN ("
            "'administrator_email_verification', "
            "'administrator_password_reset', "
            "'administrator_password_changed'"
            ") THEN user_id IS NOT NULL ELSE true END",
            name="ck_transactional_email_deliveries_identity_event_user",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_transactional_email_deliveries_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "maximum_attempts > 0",
            name="ck_transactional_email_deliveries_maximum_attempts_positive",
        ),
        CheckConstraint(
            "attempt_count <= maximum_attempts",
            name="ck_transactional_email_deliveries_attempt_limit",
        ),
        CheckConstraint(
            "CASE "
            "WHEN status IN ('pending', 'sending', 'retry_scheduled') THEN "
            "sent_at IS NULL AND failed_at IS NULL AND canceled_at IS NULL "
            "WHEN status = 'sent' THEN "
            "sent_at IS NOT NULL AND failed_at IS NULL AND canceled_at IS NULL "
            "WHEN status = 'permanently_failed' THEN "
            "failed_at IS NOT NULL AND sent_at IS NULL AND canceled_at IS NULL "
            "WHEN status = 'canceled' THEN "
            "canceled_at IS NOT NULL AND sent_at IS NULL AND failed_at IS NULL "
            "ELSE false END",
            name="ck_transactional_email_deliveries_state_timestamps",
        ),
        CheckConstraint(
            "CASE "
            "WHEN status IN ('pending', 'sending', 'retry_scheduled') THEN "
            "retention_expires_at IS NULL "
            "ELSE COALESCE("
            "retention_expires_at > COALESCE(sent_at, failed_at, canceled_at), "
            "false) END",
            name="ck_transactional_email_deliveries_retention_state",
        ),
        CheckConstraint(
            "CASE "
            "WHEN provider_message_id IS NULL THEN "
            "provider_message_id_expires_at IS NULL "
            "ELSE provider_message_id = btrim(provider_message_id) "
            "AND provider_message_id <> '' "
            "AND status = 'sent' "
            "AND COALESCE(provider_message_id_expires_at > sent_at, false) END",
            name="ck_transactional_email_deliveries_provider_message_state",
        ),
        CheckConstraint(
            "CASE "
            "WHEN safe_failure_code IS NULL THEN status <> 'permanently_failed' "
            "ELSE safe_failure_code ~ '^[a-z][a-z0-9_]{0,63}$' "
            "AND status IN ('sending', 'retry_scheduled', 'permanently_failed') END",
            name="ck_transactional_email_deliveries_safe_failure_code_state",
        ),
        Index(
            "ix_transactional_email_deliveries_status_available_at",
            "status",
            "available_at",
        ),
        Index(
            "ix_transactional_email_deliveries_retention_expires_at",
            "retention_expires_at",
            postgresql_where=text("retention_expires_at IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "installations.id",
            name="fk_transactional_email_deliveries_workspace_id_installations",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_transactional_email_deliveries_user_id_users",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    scope: Mapped[TransactionalEmailScope] = mapped_column(
        Enum(
            TransactionalEmailScope,
            name="transactional_email_scope",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )

    event_type: Mapped[TransactionalEmailEventType] = mapped_column(
        Enum(
            TransactionalEmailEventType,
            name="transactional_email_event_type",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )

    status: Mapped[TransactionalEmailDeliveryStatus] = mapped_column(
        Enum(
            TransactionalEmailDeliveryStatus,
            name="transactional_email_delivery_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=TransactionalEmailDeliveryStatus.PENDING,
        server_default=TransactionalEmailDeliveryStatus.PENDING.value,
    )

    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
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

    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
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

    safe_failure_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    provider_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    provider_message_id_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    retention_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    encrypted_recipient: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    recipient_key_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
