"""add playoff predictor settings

Revision ID: 6b8a1d4c9e72
Revises: f3a9c17d82b4
Create Date: 2026-11-17 19:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "6b8a1d4c9e72"
down_revision: str | Sequence[str] | None = "f3a9c17d82b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store canonical playoff bracket settings."""

    op.add_column(
        "schedule_rules",
        sa.Column(
            "playoff_team_count",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "schedule_rules",
        sa.Column(
            "playoff_seed_tie_rule",
            sa.String(length=100),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_schedule_rules_playoff_team_count_minimum",
        "schedule_rules",
        ("playoff_team_count IS NULL OR playoff_team_count >= 2"),
    )


def downgrade() -> None:
    """Remove canonical playoff bracket settings."""

    op.drop_constraint(
        "ck_schedule_rules_playoff_team_count_minimum",
        "schedule_rules",
        type_="check",
    )
    op.drop_column(
        "schedule_rules",
        "playoff_seed_tie_rule",
    )
    op.drop_column(
        "schedule_rules",
        "playoff_team_count",
    )
