"""Make legacy ESPN identifiers optional.

Revision ID: e5b1491618f7
Revises: e4cbbfd7df55
Create Date: 2026-11-08 21:34:03.758129
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b1491618f7"
down_revision: str | Sequence[str] | None = "e4cbbfd7df55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow non-ESPN providers to omit legacy integer identifiers."""

    op.alter_column(
        "leagues",
        "espn_league_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    op.alter_column(
        "teams",
        "espn_team_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.alter_column(
        "player_week_scores",
        "espn_player_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    """Require legacy ESPN identifiers again."""

    connection = op.get_bind()

    missing_legacy_identifiers = connection.execute(
        sa.text(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM leagues
                    WHERE espn_league_id IS NULL
                )
                OR EXISTS (
                    SELECT 1
                    FROM teams
                    WHERE espn_team_id IS NULL
                )
                OR EXISTS (
                    SELECT 1
                    FROM player_week_scores
                    WHERE espn_player_id IS NULL
                )
            """
        )
    ).scalar_one()

    if missing_legacy_identifiers:
        raise RuntimeError(
            "Cannot downgrade while non-ESPN rows have null legacy ESPN identifiers."
        )

    op.alter_column(
        "player_week_scores",
        "espn_player_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.alter_column(
        "teams",
        "espn_team_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.alter_column(
        "leagues",
        "espn_league_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
