"""add workspace ownership foundation

Revision ID: 786913fdec15
Revises: 5e32a7bc419d
Create Date: 2026-11-18 19:29:02.130029
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "786913fdec15"
down_revision: str | Sequence[str] | None = "5e32a7bc419d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXISTING_WORKSPACE_SLUG = "jimmyandfriends"


def upgrade() -> None:
    """Add and backfill the first League Workspace ownership fields."""

    connection = op.get_bind()

    installation_count = int(connection.scalar(sa.text("SELECT count(*) FROM installations")) or 0)

    if installation_count > 1:
        raise RuntimeError(
            "Workspace ownership migration requires no more than one existing installation."
        )

    op.add_column(
        "installations",
        sa.Column(
            "slug",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.add_column(
        "installations",
        sa.Column(
            "normalized_slug",
            sa.String(length=50),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "workspace_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "normalized_email",
            sa.String(length=320),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_users_workspace_id_installations",
        "users",
        "installations",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )

    if installation_count == 1:
        installation_id = connection.scalar(
            sa.text(
                """
                SELECT id
                FROM installations
                ORDER BY id
                LIMIT 1
                """
            )
        )

        connection.execute(
            sa.text(
                """
                UPDATE installations
                SET
                    slug = :slug,
                    normalized_slug = :slug
                WHERE id = :installation_id
                """
            ),
            {
                "installation_id": installation_id,
                "slug": EXISTING_WORKSPACE_SLUG,
            },
        )

        connection.execute(
            sa.text(
                """
                UPDATE users
                SET
                    workspace_id = :installation_id,
                    normalized_email = CASE
                        WHEN email IS NULL
                            OR btrim(email) = ''
                        THEN NULL
                        ELSE lower(btrim(email))
                    END,
                    email_verified_at = CASE
                        WHEN role = 'admin'
                            AND email IS NOT NULL
                            AND btrim(email) <> ''
                        THEN COALESCE(
                            last_login_at,
                            updated_at,
                            created_at,
                            CURRENT_TIMESTAMP
                        )
                        ELSE NULL
                    END
                """
            ),
            {
                "installation_id": installation_id,
            },
        )

    op.create_index(
        "uq_installations_normalized_slug",
        "installations",
        ["normalized_slug"],
        unique=True,
        postgresql_where=sa.text("normalized_slug IS NOT NULL"),
    )
    op.create_index(
        "ix_users_workspace_id",
        "users",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_users_one_admin_per_workspace",
        "users",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL AND role = 'admin'::user_role"),
    )
    op.create_index(
        "uq_users_one_spectator_per_workspace",
        "users",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL AND role = 'spectator'::user_role"),
    )
    op.create_index(
        "uq_users_admin_normalized_email",
        "users",
        ["normalized_email"],
        unique=True,
        postgresql_where=sa.text("role = 'admin'::user_role AND normalized_email IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove the League Workspace ownership foundation."""

    op.drop_index(
        "uq_users_admin_normalized_email",
        table_name="users",
    )
    op.drop_index(
        "uq_users_one_spectator_per_workspace",
        table_name="users",
    )
    op.drop_index(
        "uq_users_one_admin_per_workspace",
        table_name="users",
    )
    op.drop_index(
        "ix_users_workspace_id",
        table_name="users",
    )
    op.drop_index(
        "uq_installations_normalized_slug",
        table_name="installations",
    )

    op.drop_constraint(
        "fk_users_workspace_id_installations",
        "users",
        type_="foreignkey",
    )

    op.drop_column(
        "users",
        "email_verified_at",
    )
    op.drop_column(
        "users",
        "normalized_email",
    )
    op.drop_column(
        "users",
        "workspace_id",
    )
    op.drop_column(
        "installations",
        "normalized_slug",
    )
    op.drop_column(
        "installations",
        "slug",
    )
