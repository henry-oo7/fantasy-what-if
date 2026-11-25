"""Add canonical League and per-user Season access schema (ADR 0010).

Revision ID: f1b6d2c8a9e4
Revises: a3c9e5f1b7d2

Expand-only. No runtime code reads the new structures as authority and no
existing row is rewritten or backfilled. ``provider_connections.installation_id``
becomes nullable so a connection may be user-owned; a check requires at least
one owner. Upgrade refuses if duplicate canonical League identities exist
(no row is merged or deleted). Downgrade refuses if any target-state data
would be discarded.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1b6d2c8a9e4"
down_revision: str | Sequence[str] | None = "a3c9e5f1b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

season_sync_state = postgresql.ENUM(
    "never_synced",
    "queued",
    "syncing",
    "fresh",
    "stale",
    "failed",
    name="season_sync_state",
    create_type=False,
)
access_status = postgresql.ENUM(
    "active",
    "reverification_required",
    "revoked",
    name="user_season_access_status",
    create_type=False,
)
proof_mode = postgresql.ENUM(
    "public",
    "private_credentials",
    name="user_season_proof_mode",
    create_type=False,
)

DUPLICATE_LEAGUES = (
    "SELECT count(*) FROM (SELECT 1 FROM leagues "
    "WHERE provider IS NOT NULL AND provider_league_id IS NOT NULL "
    "GROUP BY provider, btrim(provider_league_id) HAVING count(*) > 1) d"
)


def upgrade() -> None:
    bind = op.get_bind()

    duplicates = bind.execute(sa.text(DUPLICATE_LEAGUES)).scalar_one()
    if duplicates:
        raise RuntimeError(
            f"Refusing to upgrade: {duplicates} canonical League identit(ies) are duplicated. "
            "Resolve the duplicate League rows explicitly first; none are merged or deleted here."
        )

    for enum in (season_sync_state, access_status, proof_mode):
        enum.create(bind, checkfirst=False)

    # Canonical League identity and orphan retention.
    op.add_column("leagues", sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_leagues_provider_identity",
        "leagues",
        ["provider", "provider_league_id"],
        unique=True,
        postgresql_where=sa.text("provider IS NOT NULL AND provider_league_id IS NOT NULL"),
    )
    op.create_index(
        "ix_leagues_orphaned_at",
        "leagues",
        ["orphaned_at"],
        postgresql_where=sa.text("orphaned_at IS NOT NULL"),
    )

    # Season synchronization lifecycle; legacy rows keep NULL / 0.
    op.add_column("seasons", sa.Column("sync_state", season_sync_state, nullable=True))
    op.add_column(
        "seasons",
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "seasons",
        sa.Column("last_sync_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("seasons", sa.Column("last_sync_failure_code", sa.String(64), nullable=True))
    op.add_column(
        "seasons",
        sa.Column("data_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_seasons_data_version_nonnegative", "seasons", "data_version >= 0"
    )
    op.create_check_constraint(
        "ck_seasons_sync_failure_code_safe",
        "seasons",
        "last_sync_failure_code IS NULL OR last_sync_failure_code ~ '^[a-z][a-z0-9_]{0,63}$'",
    )

    # Transitional user-owned provider connections.
    op.add_column("provider_connections", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_provider_connections_user_id_users",
        "provider_connections",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_provider_connections_user_id", "provider_connections", ["user_id"])
    op.alter_column("provider_connections", "installation_id", nullable=True)
    op.create_check_constraint(
        "ck_provider_connections_owner_boundary",
        "provider_connections",
        "installation_id IS NOT NULL OR user_id IS NOT NULL",
    )

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("default_league_season_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["default_league_season_id"], ["seasons.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_user_preferences_default_league_season_id",
        "user_preferences",
        ["default_league_season_id"],
    )

    op.create_table(
        "user_league_season_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("provider_connection_id", sa.Integer(), nullable=True),
        sa.Column("proof_mode", proof_mode, nullable=False),
        sa.Column("status", access_status, server_default="active", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reverified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider_connection_id"], ["provider_connections.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "season_id", name="uq_user_league_season_access_user_season"
        ),
        sa.CheckConstraint(
            "(proof_mode = 'public' AND provider_connection_id IS NULL) "
            "OR (proof_mode = 'private_credentials' AND provider_connection_id IS NOT NULL)",
            name="ck_user_league_season_access_proof_connection",
        ),
    )
    op.create_index(
        "ix_user_league_season_access_active_season",
        "user_league_season_access",
        ["season_id"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_user_league_season_access_connection",
        "user_league_season_access",
        ["provider_connection_id"],
        postgresql_where=sa.text("provider_connection_id IS NOT NULL"),
    )


def _refuse_if_any(bind: sa.Connection, description: str, query: str) -> None:
    count = bind.execute(sa.text(query)).scalar_one()
    if count:
        raise RuntimeError(
            f"Refusing to downgrade: {count} {description}. "
            "Remove this target-model data explicitly first."
        )


def downgrade() -> None:
    bind = op.get_bind()

    for description, query in (
        ("user_preferences row(s) exist", "SELECT count(*) FROM user_preferences"),
        (
            "user_league_season_access row(s) exist",
            "SELECT count(*) FROM user_league_season_access",
        ),
        (
            "provider connection(s) are user-owned",
            "SELECT count(*) FROM provider_connections WHERE user_id IS NOT NULL",
        ),
        (
            "provider connection(s) have no workspace owner",
            "SELECT count(*) FROM provider_connections WHERE installation_id IS NULL",
        ),
        (
            "League(s) carry orphaned_at",
            "SELECT count(*) FROM leagues WHERE orphaned_at IS NOT NULL",
        ),
        (
            "Season(s) carry non-default synchronization state",
            "SELECT count(*) FROM seasons WHERE sync_state IS NOT NULL "
            "OR last_successful_sync_at IS NOT NULL OR last_sync_attempt_at IS NOT NULL "
            "OR last_sync_failure_code IS NOT NULL OR data_version <> 0",
        ),
    ):
        _refuse_if_any(bind, description, query)

    op.drop_table("user_league_season_access")
    op.drop_table("user_preferences")

    op.drop_constraint("ck_provider_connections_owner_boundary", "provider_connections")
    op.alter_column("provider_connections", "installation_id", nullable=False)
    op.drop_index("ix_provider_connections_user_id", table_name="provider_connections")
    op.drop_constraint(
        "fk_provider_connections_user_id_users", "provider_connections", type_="foreignkey"
    )
    op.drop_column("provider_connections", "user_id")

    op.drop_constraint("ck_seasons_sync_failure_code_safe", "seasons")
    op.drop_constraint("ck_seasons_data_version_nonnegative", "seasons")
    for column in (
        "data_version",
        "last_sync_failure_code",
        "last_sync_attempt_at",
        "last_successful_sync_at",
        "sync_state",
    ):
        op.drop_column("seasons", column)

    op.drop_index("ix_leagues_orphaned_at", table_name="leagues")
    op.drop_index("uq_leagues_provider_identity", table_name="leagues")
    op.drop_column("leagues", "orphaned_at")

    for enum in (proof_mode, access_status, season_sync_state):
        enum.drop(bind, checkfirst=False)
