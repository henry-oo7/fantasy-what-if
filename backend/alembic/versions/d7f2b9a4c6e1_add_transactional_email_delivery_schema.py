"""Add transactional email delivery schema (ADR 0009).

Revision ID: d7f2b9a4c6e1
Revises: c4e8a1f7d3b9

Schema-only foundation. No runtime code sends email, generates tokens, or
handles ``transactional_email_delivery`` jobs yet. Existing jobs, workspaces,
and users are unchanged.

``ALTER TYPE ... ADD VALUE`` runs inside the migration transaction. The new
value is not referenced before commit: the job-workspace CHECK compares
``job_type::text`` so PostgreSQL does not see an unsafe use of it.
Downgrade recreates ``background_job_type`` (rename, create, cast, drop)
because PostgreSQL cannot remove an enum value, and refuses first if any
email delivery, email job, or NULL-workspace job exists.
"""

# ruff: noqa: E501  (constraint SQL is kept as single literal strings)

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d7f2b9a4c6e1"
down_revision: str | Sequence[str] | None = "c4e8a1f7d3b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS_JOB_TYPES = (
    "initial_workspace_sync",
    "incremental_season_sync",
    "full_season_sync",
    "historical_season_import",
    "provider_credential_validation",
    "provider_token_refresh",
    "provisional_workspace_cleanup",
    "expired_session_cleanup",
    "expired_token_cleanup",
    "workspace_deletion",
)

scope_enum = postgresql.ENUM(
    "workspace",
    "unscoped",
    name="transactional_email_scope",
    create_type=False,
)
event_type_enum = postgresql.ENUM(
    "administrator_email_verification",
    "administrator_password_reset",
    "administrator_password_changed",
    "workspace_deletion_requested",
    "workspace_deletion_reminder",
    "workspace_deletion_completed",
    "workspace_deletion_canceled",
    name="transactional_email_event_type",
    create_type=False,
)
status_enum = postgresql.ENUM(
    "pending",
    "sending",
    "retry_scheduled",
    "sent",
    "permanently_failed",
    "canceled",
    name="transactional_email_delivery_status",
    create_type=False,
)

DELIVERY_CHECKS = (
    (
        "ck_transactional_email_deliveries_attempt_count_nonnegative",
        "attempt_count >= 0",
    ),
    (
        "ck_transactional_email_deliveries_idempotency_key_clean",
        "idempotency_key = btrim(idempotency_key) AND idempotency_key <> ''",
    ),
    (
        "ck_transactional_email_deliveries_state_timestamps",
        "CASE WHEN status IN ('pending', 'sending', 'retry_scheduled') THEN sent_at IS NULL AND failed_at IS NULL AND canceled_at IS NULL WHEN status = 'sent' THEN sent_at IS NOT NULL AND failed_at IS NULL AND canceled_at IS NULL WHEN status = 'permanently_failed' THEN failed_at IS NOT NULL AND sent_at IS NULL AND canceled_at IS NULL WHEN status = 'canceled' THEN canceled_at IS NOT NULL AND sent_at IS NULL AND failed_at IS NULL ELSE false END",
    ),
    (
        "ck_transactional_email_deliveries_key_version_positive",
        "recipient_key_version IS NULL OR recipient_key_version > 0",
    ),
    (
        "ck_transactional_email_deliveries_safe_failure_code_state",
        "CASE WHEN safe_failure_code IS NULL THEN status <> 'permanently_failed' ELSE safe_failure_code ~ '^[a-z][a-z0-9_]{0,63}$' AND status IN ('sending', 'retry_scheduled', 'permanently_failed') END",
    ),
    (
        "ck_transactional_email_deliveries_maximum_attempts_positive",
        "maximum_attempts > 0",
    ),
    (
        "ck_transactional_email_deliveries_provider_message_state",
        "CASE WHEN provider_message_id IS NULL THEN provider_message_id_expires_at IS NULL ELSE provider_message_id = btrim(provider_message_id) AND provider_message_id <> '' AND status = 'sent' AND COALESCE(provider_message_id_expires_at > sent_at, false) END",
    ),
    (
        "ck_transactional_email_deliveries_scope_shape",
        "CASE WHEN scope = 'workspace' THEN workspace_id IS NOT NULL AND encrypted_recipient IS NULL AND recipient_key_version IS NULL AND event_type <> 'workspace_deletion_completed' WHEN scope = 'unscoped' THEN workspace_id IS NULL AND user_id IS NULL AND event_type = 'workspace_deletion_completed' AND ((status IN ('pending', 'sending', 'retry_scheduled')) = (encrypted_recipient IS NOT NULL)) ELSE false END",
    ),
    (
        "ck_transactional_email_deliveries_retention_state",
        "CASE WHEN status IN ('pending', 'sending', 'retry_scheduled') THEN retention_expires_at IS NULL ELSE COALESCE(retention_expires_at > COALESCE(sent_at, failed_at, canceled_at), false) END",
    ),
    (
        "ck_transactional_email_deliveries_identity_event_user",
        "CASE WHEN event_type IN ('administrator_email_verification', 'administrator_password_reset', 'administrator_password_changed') THEN user_id IS NOT NULL ELSE true END",
    ),
    (
        "ck_transactional_email_deliveries_attempt_limit",
        "attempt_count <= maximum_attempts",
    ),
    (
        "ck_transactional_email_deliveries_recipient_pair",
        "(encrypted_recipient IS NULL) = (recipient_key_version IS NULL)",
    ),
)

JOB_CHECKS = (
    (
        "ck_background_jobs_transactional_email_payload_shape",
        "job_type::text <> 'transactional_email_delivery' OR COALESCE(jsonb_typeof(payload) = 'object' AND (payload - 'version' - 'delivery_id') = '{}'::jsonb AND payload ? 'version' AND payload ? 'delivery_id' AND jsonb_typeof(payload -> 'version') = 'number' AND jsonb_typeof(payload -> 'delivery_id') = 'number', false)",
    ),
    (
        "ck_background_jobs_workspace_required_except_email",
        "workspace_id IS NOT NULL OR job_type::text = 'transactional_email_delivery'",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()

    scope_enum.create(bind, checkfirst=False)
    event_type_enum.create(bind, checkfirst=False)
    status_enum.create(bind, checkfirst=False)

    op.create_table(
        "transactional_email_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("scope", scope_enum, nullable=False),
        sa.Column("event_type", event_type_enum, nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="pending"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("maximum_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_failure_code", sa.String(length=64), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("provider_message_id_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("encrypted_recipient", sa.Text(), nullable=True),
        sa.Column("recipient_key_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["installations.id"],
            name="fk_transactional_email_deliveries_workspace_id_installations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_transactional_email_deliveries_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_transactional_email_deliveries_idempotency_key",
        ),
        *(sa.CheckConstraint(sql, name=name) for name, sql in DELIVERY_CHECKS),
    )
    op.create_index(
        "ix_transactional_email_deliveries_workspace_id",
        "transactional_email_deliveries",
        ["workspace_id"],
    )
    op.create_index(
        "ix_transactional_email_deliveries_user_id",
        "transactional_email_deliveries",
        ["user_id"],
    )
    op.create_index(
        "ix_transactional_email_deliveries_status_available_at",
        "transactional_email_deliveries",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_transactional_email_deliveries_retention_expires_at",
        "transactional_email_deliveries",
        ["retention_expires_at"],
        postgresql_where=sa.text("retention_expires_at IS NOT NULL"),
    )

    op.execute(sa.text("ALTER TYPE background_job_type ADD VALUE 'transactional_email_delivery'"))
    op.alter_column(
        "background_jobs",
        "workspace_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    for name, sql in JOB_CHECKS:
        op.create_check_constraint(name, "background_jobs", sql)


def _refuse_if_any(bind: sa.Connection, description: str, query: str) -> None:
    count = bind.execute(sa.text(query)).scalar_one()

    if count:
        raise RuntimeError(
            f"Refusing to downgrade: {count} {description}. "
            "Resolve or remove this transactional-email state explicitly first."
        )


def downgrade() -> None:
    bind = op.get_bind()

    _refuse_if_any(
        bind,
        "transactional email delivery row(s) exist",
        "SELECT count(*) FROM transactional_email_deliveries",
    )
    _refuse_if_any(
        bind,
        "transactional_email_delivery background job(s) exist",
        "SELECT count(*) FROM background_jobs "
        "WHERE job_type::text = 'transactional_email_delivery'",
    )
    _refuse_if_any(
        bind,
        "background job(s) have a NULL workspace_id",
        "SELECT count(*) FROM background_jobs WHERE workspace_id IS NULL",
    )

    for name, _sql in reversed(JOB_CHECKS):
        op.drop_constraint(name, "background_jobs", type_="check")

    op.alter_column(
        "background_jobs",
        "workspace_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.drop_index(
        "ix_transactional_email_deliveries_retention_expires_at",
        table_name="transactional_email_deliveries",
    )
    op.drop_index(
        "ix_transactional_email_deliveries_status_available_at",
        table_name="transactional_email_deliveries",
    )
    op.drop_index(
        "ix_transactional_email_deliveries_user_id",
        table_name="transactional_email_deliveries",
    )
    op.drop_index(
        "ix_transactional_email_deliveries_workspace_id",
        table_name="transactional_email_deliveries",
    )
    op.drop_table("transactional_email_deliveries")

    status_enum.drop(bind, checkfirst=False)
    event_type_enum.drop(bind, checkfirst=False)
    scope_enum.drop(bind, checkfirst=False)

    op.execute(sa.text("ALTER TYPE background_job_type RENAME TO background_job_type_old"))
    previous_values = ", ".join(f"'{value}'" for value in PREVIOUS_JOB_TYPES)
    op.execute(sa.text(f"CREATE TYPE background_job_type AS ENUM ({previous_values})"))
    op.execute(
        sa.text(
            "ALTER TABLE background_jobs ALTER COLUMN job_type "
            "TYPE background_job_type "
            "USING job_type::text::background_job_type"
        )
    )
    op.execute(sa.text("DROP TYPE background_job_type_old"))
