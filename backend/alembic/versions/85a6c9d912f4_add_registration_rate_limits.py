"""Add privacy-preserving registration rate limits.

Revision ID: 85a6c9d912f4
Revises: 3544eff485d2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "85a6c9d912f4"
down_revision: str | Sequence[str] | None = "3544eff485d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add independent counters for public registration actions."""

    op.create_table(
        "registration_rate_limits",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "action",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "identity_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "request_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_request_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "action",
            "identity_hash",
            name=("uq_registration_rate_limits_action_identity"),
        ),
    )
    op.create_index(
        "ix_registration_rate_limits_last_request_at",
        "registration_rate_limits",
        ["last_request_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove public registration action counters."""

    op.drop_index(
        "ix_registration_rate_limits_last_request_at",
        table_name="registration_rate_limits",
    )
    op.drop_table("registration_rate_limits")
