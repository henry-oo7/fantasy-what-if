"""add persistent login throttling

Revision ID: f3a9c17d82b4
Revises: 8e8d3525e870
Create Date: 2026-11-10 17:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a9c17d82b4"
down_revision: str | Sequence[str] | None = "8e8d3525e870"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create privacy-preserving login throttle state."""

    op.create_table(
        "login_attempts",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "username_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "source_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "failed_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "first_failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0",
            name="ck_login_attempts_failed_attempts_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "username_hash",
            "source_hash",
            name="uq_login_attempts_username_source",
        ),
    )

    op.create_index(
        "ix_login_attempts_locked_until",
        "login_attempts",
        ["locked_until"],
        unique=False,
    )
    op.create_index(
        "ix_login_attempts_last_failed_at",
        "login_attempts",
        ["last_failed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove persistent login throttle state."""

    op.drop_index(
        "ix_login_attempts_last_failed_at",
        table_name="login_attempts",
    )
    op.drop_index(
        "ix_login_attempts_locked_until",
        table_name="login_attempts",
    )
    op.drop_table("login_attempts")
