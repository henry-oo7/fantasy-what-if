"""add standings tiebreaker fallback

Revision ID: 5e32a7bc419d
Revises: 6b8a1d4c9e72
Create Date: 2026-11-17 14:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5e32a7bc419d"
down_revision: str | Sequence[str] | None = "6b8a1d4c9e72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store an administrator fallback separately from provider settings."""

    op.add_column(
        "schedule_rules",
        sa.Column(
            "playoff_seed_tie_rule_fallback",
            sa.String(length=100),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove the administrator standings tiebreaker fallback."""

    op.drop_column(
        "schedule_rules",
        "playoff_seed_tie_rule_fallback",
    )
