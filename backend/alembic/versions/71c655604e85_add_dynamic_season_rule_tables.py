"""Add dynamic season rule tables.

Revision ID: 71c655604e85
Revises: 526d030cc84a
Create Date: 2026-11-08 01:53:02.685639
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "71c655604e85"
down_revision: str | Sequence[str] | None = "526d030cc84a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add season-owned lineup, scoring, and schedule rules."""

    op.create_table(
        "lineup_slot_definitions",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "season_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "provider_slot_code",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "canonical_slot_code",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "slot_order",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "starter_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_starter",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_bench",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_injured_reserve",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=True,
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
        sa.CheckConstraint(
            "slot_order >= 0",
            name=("ck_lineup_slot_definitions_slot_order_nonnegative"),
        ),
        sa.CheckConstraint(
            "starter_count >= 0",
            name=("ck_lineup_slot_definitions_starter_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
            name=("fk_lineup_slot_definitions_season_id_seasons"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "season_id",
            "provider_slot_code",
            name=("uq_lineup_slot_definitions_season_provider_slot"),
        ),
    )

    op.create_index(
        "ix_lineup_slot_definitions_season_id",
        "lineup_slot_definitions",
        ["season_id"],
    )

    op.create_table(
        "schedule_rules",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "season_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "regular_season_weeks",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "playoff_week_start",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "playoff_week_end",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "has_divisions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "has_median_game",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "has_doubleheaders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "allows_ties",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=True,
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
            ["season_id"],
            ["seasons.id"],
            name=("fk_schedule_rules_season_id_seasons"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "season_id",
            name="uq_schedule_rules_season",
        ),
    )

    op.create_index(
        "ix_schedule_rules_season_id",
        "schedule_rules",
        ["season_id"],
    )

    op.create_table(
        "scoring_rules",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "season_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "provider_stat_code",
            sa.String(length=150),
            nullable=False,
        ),
        sa.Column(
            "canonical_stat_code",
            sa.String(length=150),
            nullable=True,
        ),
        sa.Column(
            "display_name",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "points_per_unit",
            sa.Numeric(
                precision=12,
                scale=6,
            ),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=True,
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
            ["season_id"],
            ["seasons.id"],
            name=("fk_scoring_rules_season_id_seasons"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "season_id",
            "provider_stat_code",
            name=("uq_scoring_rules_season_provider_stat"),
        ),
    )

    op.create_index(
        "ix_scoring_rules_season_id",
        "scoring_rules",
        ["season_id"],
    )

    op.create_table(
        "lineup_slot_eligibility",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "lineup_slot_definition_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "canonical_position",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["lineup_slot_definition_id"],
            ["lineup_slot_definitions.id"],
            name=("fk_lineup_slot_eligibility_definition_id_definitions"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "lineup_slot_definition_id",
            "canonical_position",
            name=("uq_lineup_slot_eligibility_slot_position"),
        ),
    )

    op.create_index(
        ("ix_lineup_slot_eligibility_lineup_slot_definition_id"),
        "lineup_slot_eligibility",
        ["lineup_slot_definition_id"],
    )

    # The current database contains an authoritative regular-season
    # length on each season. Preserve that value in the new normalized
    # schedule table without guessing unsupported provider settings.
    op.execute(
        sa.text(
            """
            INSERT INTO schedule_rules (
                season_id,
                regular_season_weeks,
                playoff_week_start,
                playoff_week_end,
                has_divisions,
                has_median_game,
                has_doubleheaders,
                allows_ties,
                metadata
            )
            SELECT
                season.id,
                season.regular_season_weeks,
                season.playoff_week_start,
                season.playoff_week_end,
                false,
                false,
                false,
                true,
                NULL
            FROM seasons AS season
            WHERE NOT EXISTS (
                SELECT 1
                FROM schedule_rules
                WHERE
                    schedule_rules.season_id =
                    season.id
            )
            """
        )
    )

    # Lineup-slot and scoring rules are intentionally not guessed from
    # player-week rows. The ESPN provider adapter will import the
    # authoritative league configuration in a later milestone.


def downgrade() -> None:
    """Remove dynamic season-rule tables."""

    op.drop_index(
        ("ix_lineup_slot_eligibility_lineup_slot_definition_id"),
        table_name="lineup_slot_eligibility",
    )
    op.drop_table("lineup_slot_eligibility")

    op.drop_index(
        "ix_scoring_rules_season_id",
        table_name="scoring_rules",
    )
    op.drop_table("scoring_rules")

    op.drop_index(
        "ix_schedule_rules_season_id",
        table_name="schedule_rules",
    )
    op.drop_table("schedule_rules")

    op.drop_index(
        "ix_lineup_slot_definitions_season_id",
        table_name="lineup_slot_definitions",
    )
    op.drop_table("lineup_slot_definitions")
