"""Add canonical provider identifier constraints.

Revision ID: e4cbbfd7df55
Revises: 274f44c1c419
Create Date: 2026-11-08 04:50:17.233633
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e4cbbfd7df55"
down_revision: str | Sequence[str] | None = "274f44c1c419"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enforce canonical provider identifier uniqueness."""

    op.create_unique_constraint(
        ("uq_player_week_scores_season_week_team_provider_player"),
        "player_week_scores",
        (
            "season_id",
            "week",
            "team_id",
            "provider_player_id",
        ),
    )

    op.create_unique_constraint(
        "uq_teams_season_provider_team",
        "teams",
        (
            "season_id",
            "provider_team_id",
        ),
    )


def downgrade() -> None:
    """Remove canonical provider identifier constraints."""

    op.drop_constraint(
        "uq_teams_season_provider_team",
        "teams",
        type_="unique",
    )

    op.drop_constraint(
        ("uq_player_week_scores_season_week_team_provider_player"),
        "player_week_scores",
        type_="unique",
    )
