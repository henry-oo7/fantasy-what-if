"""Add canonical teams, franchises, and player identifiers.

Revision ID: 526d030cc84a
Revises: 9777067a4aeb
Create Date: 2026-11-08 01:44:37.110414
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "526d030cc84a"
down_revision: str | Sequence[str] | None = "9777067a4aeb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add canonical season-team and player identifiers."""

    op.create_table(
        "franchises",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "league_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "logo_url",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name="fk_franchises_league_id_leagues",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_franchises_league_id",
        "franchises",
        ["league_id"],
    )

    op.add_column(
        "teams",
        sa.Column(
            "provider_team_id",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "teams",
        sa.Column(
            "franchise_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "teams",
        sa.Column(
            "abbreviation",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.add_column(
        "teams",
        sa.Column(
            "owner_display_name",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "teams",
        sa.Column(
            "logo_url",
            sa.String(length=1000),
            nullable=True,
        ),
    )
    op.add_column(
        "teams",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    op.create_index(
        "ix_teams_franchise_id",
        "teams",
        ["franchise_id"],
    )
    op.create_foreign_key(
        "fk_teams_franchise_id_franchises",
        "teams",
        "franchises",
        ["franchise_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "player_week_scores",
        sa.Column(
            "provider_player_id",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "player_week_scores",
        sa.Column(
            "provider_position",
            sa.String(length=100),
            nullable=True,
        ),
    )

    # Backfill provider-independent string identifiers from the
    # preserved ESPN identifiers.
    op.execute(
        sa.text(
            """
            UPDATE teams
            SET
                provider_team_id =
                    espn_team_id::text,
                is_active = true
            WHERE provider_team_id IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE player_week_scores
            SET
                provider_player_id =
                    espn_player_id::text,
                provider_position =
                    default_position
            WHERE provider_player_id IS NULL
               OR provider_position IS NULL
            """
        )
    )

    # Franchises are intentionally not generated automatically.
    # Cross-season continuity requires administrator confirmation
    # during the new-season workflow.


def downgrade() -> None:
    """Remove canonical team and player compatibility fields."""

    op.drop_column(
        "player_week_scores",
        "provider_position",
    )
    op.drop_column(
        "player_week_scores",
        "provider_player_id",
    )

    op.drop_constraint(
        "fk_teams_franchise_id_franchises",
        "teams",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_teams_franchise_id",
        table_name="teams",
    )

    for column_name in (
        "is_active",
        "logo_url",
        "owner_display_name",
        "abbreviation",
        "franchise_id",
        "provider_team_id",
    ):
        op.drop_column(
            "teams",
            column_name,
        )

    op.drop_index(
        "ix_franchises_league_id",
        table_name="franchises",
    )
    op.drop_table("franchises")
