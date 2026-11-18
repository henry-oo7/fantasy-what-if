"""add background job foundation

Revision ID: db0801db5da3
Revises: 9d5b27f1a1df
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "db0801db5da3"
down_revision: str | Sequence[str] | None = "9d5b27f1a1df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_JOB_STATUSES = (
    "pending",
    "running",
    "retry_scheduled",
)


def upgrade() -> None:
    """Add durable workspace-owned background jobs."""

    background_job_type = postgresql.ENUM(
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
        name="background_job_type",
        create_type=False,
    )
    background_job_status = postgresql.ENUM(
        "pending",
        "running",
        "retry_scheduled",
        "succeeded",
        "failed",
        "canceled",
        name="background_job_status",
        create_type=False,
    )

    background_job_type.create(
        op.get_bind(),
        checkfirst=True,
    )
    background_job_status.create(
        op.get_bind(),
        checkfirst=True,
    )

    op.create_table(
        "background_jobs",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "job_type",
            background_job_type,
            nullable=False,
        ),
        sa.Column(
            "status",
            background_job_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "payload_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "progress",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=True,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "maximum_attempts",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "claimed_by",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "failed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "canceled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "safe_error_code",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "safe_error_message",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "retryable",
            sa.Boolean(),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            "payload_version > 0",
            name="ck_background_jobs_payload_version_positive",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_background_jobs_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "maximum_attempts > 0",
            name="ck_background_jobs_maximum_attempts_positive",
        ),
        sa.CheckConstraint(
            "attempt_count <= maximum_attempts",
            name="ck_background_jobs_attempt_limit",
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR idempotency_key = btrim(idempotency_key)",
            name="ck_background_jobs_idempotency_key_trimmed",
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR idempotency_key <> ''",
            name="ck_background_jobs_idempotency_key_nonempty",
        ),
        sa.CheckConstraint(
            "status <> 'running'::background_job_status "
            "OR ("
            "claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND claimed_by IS NOT NULL "
            "AND started_at IS NOT NULL"
            ")",
            name="ck_background_jobs_running_claim",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded'::background_job_status OR completed_at IS NOT NULL",
            name="ck_background_jobs_succeeded_timestamp",
        ),
        sa.CheckConstraint(
            "status <> 'failed'::background_job_status OR failed_at IS NOT NULL",
            name="ck_background_jobs_failed_timestamp",
        ),
        sa.CheckConstraint(
            "status <> 'canceled'::background_job_status OR canceled_at IS NOT NULL",
            name="ck_background_jobs_canceled_timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["installations.id"],
            name=("fk_background_jobs_workspace_id_installations"),
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_background_jobs_workspace_id",
        "background_jobs",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_background_jobs_queue_order",
        "background_jobs",
        [
            "status",
            sa.text("priority DESC"),
            "available_at",
            "id",
        ],
        unique=False,
    )
    op.create_index(
        "uq_background_jobs_active_idempotency_key",
        "background_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            "idempotency_key IS NOT NULL "
            "AND status IN ("
            "'pending'::background_job_status, "
            "'running'::background_job_status, "
            "'retry_scheduled'::background_job_status"
            ")"
        ),
    )


def downgrade() -> None:
    """Remove durable background jobs."""

    op.drop_index(
        "uq_background_jobs_active_idempotency_key",
        table_name="background_jobs",
    )
    op.drop_index(
        "ix_background_jobs_queue_order",
        table_name="background_jobs",
    )
    op.drop_index(
        "ix_background_jobs_workspace_id",
        table_name="background_jobs",
    )
    op.drop_table("background_jobs")

    postgresql.ENUM(
        name="background_job_status",
    ).drop(
        op.get_bind(),
        checkfirst=True,
    )
    postgresql.ENUM(
        name="background_job_type",
    ).drop(
        op.get_bind(),
        checkfirst=True,
    )
