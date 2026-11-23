"""Add workspace deletion schema foundation (FR-20).

Revision ID: c4e8a1f7d3b9
Revises: b7d41e9c2a10

Additive, schema-only. No runtime code issues deletion_recovery sessions,
uses the workspace_deletion_requested revocation reason, or writes deletion
state or records yet. Existing workspaces and sessions are unchanged.

``ALTER TYPE ... ADD VALUE`` runs inside the migration transaction. That
is permitted on PostgreSQL 12+ because the new value is not used before
commit. Downgrade recreates the enum (rename, create, cast, drop) because
PostgreSQL cannot remove an enum value.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4e8a1f7d3b9"
down_revision: str | Sequence[str] | None = "b7d41e9c2a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


auth_session_purpose = postgresql.ENUM(
    "standard",
    "deletion_recovery",
    name="auth_session_purpose",
    create_type=False,
)

fantasy_provider = postgresql.ENUM(
    "espn",
    "sleeper",
    "yahoo",
    name="fantasy_provider",
    create_type=False,
)

PREVIOUS_REVOCATION_REASONS = (
    "replaced",
    "logout",
    "password_reset",
    "spectator_password_reset",
    "spectator_disabled",
)

DELETION_LIFECYCLE_CHECK = (
    "CASE WHEN registration_status = 'deletion_pending'::workspace_registration_status "
    "THEN deletion_requested_at IS NOT NULL "
    "AND deletion_scheduled_for IS NOT NULL "
    "AND deletion_scheduled_for > deletion_requested_at "
    "ELSE deletion_requested_at IS NULL "
    "AND deletion_scheduled_for IS NULL END"
)
PENDING_PREDICATE = "registration_status = 'deletion_pending'::workspace_registration_status"


def upgrade() -> None:
    bind = op.get_bind()

    # A. installations deletion timestamps, lifecycle check, due index.
    op.add_column(
        "installations",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "installations",
        sa.Column("deletion_scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_installations_deletion_lifecycle",
        "installations",
        DELETION_LIFECYCLE_CHECK,
    )
    op.create_index(
        "ix_installations_deletion_scheduled_for_pending",
        "installations",
        ["deletion_scheduled_for"],
        unique=False,
        postgresql_where=sa.text(PENDING_PREDICATE),
    )

    # B. auth session purpose: add nullable, backfill, then enforce.
    auth_session_purpose.create(bind, checkfirst=False)
    op.add_column(
        "auth_sessions",
        sa.Column(
            "purpose",
            auth_session_purpose,
            nullable=True,
            server_default="standard",
        ),
    )
    op.execute(
        sa.text(
            "UPDATE auth_sessions SET purpose = 'standard'::auth_session_purpose "
            "WHERE purpose IS NULL"
        )
    )
    op.alter_column(
        "auth_sessions",
        "purpose",
        existing_type=auth_session_purpose,
        nullable=False,
        existing_server_default=sa.text("'standard'::auth_session_purpose"),
    )

    # C. session revocation reason.
    op.execute(sa.text("ALTER TYPE session_revocation_reason ADD VALUE 'workspace_deletion_requested'"))

    # D. anonymized deletion records (no foreign key to installations).
    op.create_table(
        "workspace_deletion_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("former_workspace_id", sa.Integer(), nullable=False),
        sa.Column("provider", fantasy_provider, nullable=False),
        sa.Column("normalized_provider_league_id", sa.String(length=255), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "scheduled_for > requested_at",
            name="ck_workspace_deletion_records_scheduled_after_requested",
        ),
        sa.CheckConstraint(
            "completed_at >= scheduled_for",
            name="ck_workspace_deletion_records_completed_after_scheduled",
        ),
        sa.CheckConstraint(
            "retention_expires_at > completed_at",
            name="ck_workspace_deletion_records_retention_after_completed",
        ),
        sa.CheckConstraint(
            "btrim(normalized_provider_league_id) <> ''",
            name="ck_workspace_deletion_records_league_id_not_blank",
        ),
        sa.CheckConstraint(
            "outcome IN ('completed')",
            name="ck_workspace_deletion_records_outcome",
        ),
    )
    op.create_index(
        "ix_workspace_deletion_records_former_workspace_id",
        "workspace_deletion_records",
        ["former_workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_deletion_records_retention_expires_at",
        "workspace_deletion_records",
        ["retention_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_deletion_records_provider_identity",
        "workspace_deletion_records",
        ["provider", "normalized_provider_league_id"],
        unique=False,
    )


def _refuse_if_any(bind: sa.Connection, description: str, query: str) -> None:
    count = bind.execute(sa.text(query)).scalar_one()

    if count:
        raise RuntimeError(
            f"Refusing to downgrade: {count} {description}. "
            "Resolve or remove this FR-20 deletion state explicitly first."
        )


def downgrade() -> None:
    bind = op.get_bind()

    _refuse_if_any(
        bind,
        "workspace(s) carry deletion timestamps",
        "SELECT count(*) FROM installations "
        "WHERE deletion_requested_at IS NOT NULL OR deletion_scheduled_for IS NOT NULL",
    )
    _refuse_if_any(
        bind,
        "workspace(s) are deletion_pending",
        "SELECT count(*) FROM installations "
        "WHERE registration_status = 'deletion_pending'::workspace_registration_status",
    )
    _refuse_if_any(
        bind,
        "auth session(s) have a non-standard purpose",
        "SELECT count(*) FROM auth_sessions "
        "WHERE purpose <> 'standard'::auth_session_purpose",
    )
    _refuse_if_any(
        bind,
        "auth session(s) use the workspace_deletion_requested revocation reason",
        "SELECT count(*) FROM auth_sessions "
        "WHERE revocation_reason = 'workspace_deletion_requested'::session_revocation_reason",
    )
    _refuse_if_any(
        bind,
        "workspace deletion record(s) exist",
        "SELECT count(*) FROM workspace_deletion_records",
    )

    op.drop_index(
        "ix_workspace_deletion_records_provider_identity",
        table_name="workspace_deletion_records",
    )
    op.drop_index(
        "ix_workspace_deletion_records_retention_expires_at",
        table_name="workspace_deletion_records",
    )
    op.drop_index(
        "ix_workspace_deletion_records_former_workspace_id",
        table_name="workspace_deletion_records",
    )
    op.drop_table("workspace_deletion_records")

    # Recreate session_revocation_reason without the new value.
    op.execute(sa.text("ALTER TYPE session_revocation_reason RENAME TO session_revocation_reason_old"))
    previous_values = ", ".join(f"'{value}'" for value in PREVIOUS_REVOCATION_REASONS)
    op.execute(sa.text(f"CREATE TYPE session_revocation_reason AS ENUM ({previous_values})"))
    op.execute(
        sa.text(
            "ALTER TABLE auth_sessions ALTER COLUMN revocation_reason "
            "TYPE session_revocation_reason "
            "USING revocation_reason::text::session_revocation_reason"
        )
    )
    op.execute(sa.text("DROP TYPE session_revocation_reason_old"))

    op.drop_column("auth_sessions", "purpose")
    auth_session_purpose.drop(bind, checkfirst=False)

    op.drop_index(
        "ix_installations_deletion_scheduled_for_pending",
        table_name="installations",
    )
    op.drop_constraint(
        "ck_installations_deletion_lifecycle",
        "installations",
        type_="check",
    )
    op.drop_column("installations", "deletion_scheduled_for")
    op.drop_column("installations", "deletion_requested_at")
