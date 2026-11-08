"""Replace member access with spectator access.

Revision ID: a5f003058442
Revises: e5b1491618f7
Create Date: 2026-11-08 22:20:23.568047
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a5f003058442"
down_revision: str | Sequence[str] | None = "e5b1491618f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Adopt administrator and spectator access."""

    op.drop_table("registration_invites")
    op.drop_table("user_team_memberships")

    op.execute(
        sa.text(
            """
            DROP TYPE invitation_status
            """
        )
    )

    op.alter_column(
        "users",
        "role",
        existing_type=postgresql.ENUM(
            "member",
            "admin",
            name="user_role",
            create_type=False,
        ),
        server_default=None,
        existing_nullable=False,
    )

    op.execute(
        sa.text(
            """
            ALTER TYPE user_role
            RENAME VALUE 'member' TO 'spectator'
            """
        )
    )

    op.alter_column(
        "users",
        "role",
        existing_type=postgresql.ENUM(
            "spectator",
            "admin",
            name="user_role",
            create_type=False,
        ),
        server_default=sa.text("'spectator'::user_role"),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Restore legacy member, invitation, and membership structures.

    Invitation and team-membership rows removed during upgrade cannot
    be reconstructed. The recreated tables are empty.
    """

    op.alter_column(
        "users",
        "role",
        existing_type=postgresql.ENUM(
            "spectator",
            "admin",
            name="user_role",
            create_type=False,
        ),
        server_default=None,
        existing_nullable=False,
    )

    op.execute(
        sa.text(
            """
            ALTER TYPE user_role
            RENAME VALUE 'spectator' TO 'member'
            """
        )
    )

    op.alter_column(
        "users",
        "role",
        existing_type=postgresql.ENUM(
            "member",
            "admin",
            name="user_role",
            create_type=False,
        ),
        server_default=sa.text("'member'::user_role"),
        existing_nullable=False,
    )

    invitation_status = postgresql.ENUM(
        "active",
        "used",
        "revoked",
        name="invitation_status",
        create_type=False,
    )

    invitation_status.create(
        op.get_bind(),
        checkfirst=False,
    )

    op.create_table(
        "registration_invites",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "token_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "season_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "intended_display_name",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "status",
            invitation_status,
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "used_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["used_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_registration_invites_created_by_user_id",
        "registration_invites",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_registration_invites_expires_at",
        "registration_invites",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_registration_invites_season_id",
        "registration_invites",
        ["season_id"],
        unique=False,
    )
    op.create_index(
        "ix_registration_invites_status",
        "registration_invites",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_registration_invites_team_id",
        "registration_invites",
        ["team_id"],
        unique=False,
    )
    op.create_index(
        "ix_registration_invites_token_hash",
        "registration_invites",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_registration_invites_used_by_user_id",
        "registration_invites",
        ["used_by_user_id"],
        unique=False,
    )

    op.create_table(
        "user_team_memberships",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "season_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "season_id",
            name="uq_user_team_memberships_user_season",
        ),
    )

    op.create_index(
        "ix_user_team_memberships_season_id",
        "user_team_memberships",
        ["season_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_team_memberships_season_team",
        "user_team_memberships",
        ["season_id", "team_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_team_memberships_team_id",
        "user_team_memberships",
        ["team_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_team_memberships_user_id",
        "user_team_memberships",
        ["user_id"],
        unique=False,
    )
