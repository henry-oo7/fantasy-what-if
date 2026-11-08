"""Add canonical installation and season foundation.

Revision ID: 9777067a4aeb
Revises: 12c9cf9bbb5a
Create Date: 2026-11-08 01:30:30.395044
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9777067a4aeb"
down_revision: str | Sequence[str] | None = "12c9cf9bbb5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


installation_status = postgresql.ENUM(
    "setup_required",
    "setup_in_progress",
    "active",
    name="installation_status",
    create_type=False,
)

fantasy_provider = postgresql.ENUM(
    "espn",
    "sleeper",
    "yahoo",
    name="fantasy_provider",
    create_type=False,
)

fantasy_sport = postgresql.ENUM(
    "football",
    name="fantasy_sport",
    create_type=False,
)

season_status = postgresql.ENUM(
    "draft",
    "active",
    "completed",
    "archived",
    name="season_status",
    create_type=False,
)


def upgrade() -> None:
    """Add and backfill the canonical platform foundation."""

    bind = op.get_bind()

    installation_status.create(
        bind,
        checkfirst=True,
    )
    fantasy_provider.create(
        bind,
        checkfirst=True,
    )
    fantasy_sport.create(
        bind,
        checkfirst=True,
    )
    season_status.create(
        bind,
        checkfirst=True,
    )

    op.create_table(
        "installations",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "application_name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "public_url",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "timezone",
            sa.String(length=100),
            nullable=False,
            server_default="America/Chicago",
        ),
        sa.Column(
            "status",
            installation_status,
            nullable=False,
            server_default="setup_required",
        ),
        sa.Column(
            "setup_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "setup_completed_at",
            sa.DateTime(timezone=True),
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
    )

    op.create_table(
        "provider_connections",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "installation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "provider",
            fantasy_provider,
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "encrypted_credentials",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "credential_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "last_validated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_validation_error",
            sa.Text(),
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
            ["installation_id"],
            ["installations.id"],
            name=("fk_provider_connections_installation_id_installations"),
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_provider_connections_installation_id",
        "provider_connections",
        ["installation_id"],
    )
    op.create_index(
        "ix_provider_connections_is_active",
        "provider_connections",
        ["is_active"],
    )
    op.create_index(
        "ix_provider_connections_provider",
        "provider_connections",
        ["provider"],
    )

    op.add_column(
        "leagues",
        sa.Column(
            "installation_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "leagues",
        sa.Column(
            "provider",
            fantasy_provider,
            nullable=True,
        ),
    )
    op.add_column(
        "leagues",
        sa.Column(
            "provider_league_id",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "leagues",
        sa.Column(
            "sport",
            fantasy_sport,
            nullable=True,
        ),
    )
    op.add_column(
        "leagues",
        sa.Column(
            "logo_url",
            sa.String(length=1000),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_leagues_installation_id",
        "leagues",
        ["installation_id"],
    )
    op.create_foreign_key(
        "fk_leagues_installation_id_installations",
        "leagues",
        "installations",
        ["installation_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "seasons",
        sa.Column(
            "provider_connection_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "provider_season_id",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "status",
            season_status,
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "team_count",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "playoff_week_start",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "playoff_week_end",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "scoring_type",
            sa.String(length=100),
            nullable=True,
        ),
    )

    capability_columns = (
        "supports_head_to_head",
        "supports_weekly_matchups",
        "supports_lineup_history",
        "supports_optimal_lineups",
        "supports_schedule_swap",
        "supports_all_play",
        "supports_league_average",
        "supports_team_logos",
    )

    for column_name in capability_columns:
        op.add_column(
            "seasons",
            sa.Column(
                column_name,
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    op.add_column(
        "seasons",
        sa.Column(
            "configuration_snapshot",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_seasons_provider_connection_id",
        "seasons",
        ["provider_connection_id"],
    )
    op.create_index(
        "ix_seasons_status",
        "seasons",
        ["status"],
    )
    op.create_foreign_key(
        ("fk_seasons_provider_connection_id_provider_connections"),
        "seasons",
        "provider_connections",
        ["provider_connection_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Create one migrated installation only when the database
    # contains the preserved single-league application.
    op.execute(
        sa.text(
            """
            INSERT INTO installations (
                application_name,
                public_url,
                timezone,
                status,
                setup_started_at,
                setup_completed_at
            )
            SELECT
                'Fantasy What-If',
                NULL,
                'America/Chicago',
                'active'::installation_status,
                now(),
                now()
            WHERE EXISTS (
                SELECT 1
                FROM leagues
            )
            AND NOT EXISTS (
                SELECT 1
                FROM installations
            )
            """
        )
    )

    # Preserve the current environment-based ESPN credentials.
    # Credential encryption and database credential storage are
    # introduced in a later migration.
    op.execute(
        sa.text(
            """
            INSERT INTO provider_connections (
                installation_id,
                provider,
                display_name,
                encrypted_credentials,
                credential_version,
                is_active
            )
            SELECT
                installation.id,
                'espn'::fantasy_provider,
                'Migrated ESPN connection',
                NULL,
                1,
                true
            FROM installations AS installation
            WHERE EXISTS (
                SELECT 1
                FROM leagues
            )
            AND NOT EXISTS (
                SELECT 1
                FROM provider_connections
                WHERE provider = 'espn'::fantasy_provider
            )
            ORDER BY installation.id
            LIMIT 1
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE leagues
            SET
                installation_id = (
                    SELECT id
                    FROM installations
                    ORDER BY id
                    LIMIT 1
                ),
                provider = 'espn'::fantasy_provider,
                provider_league_id =
                    espn_league_id::text,
                sport = 'football'::fantasy_sport
            WHERE installation_id IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE seasons
            SET
                provider_connection_id = (
                    SELECT id
                    FROM provider_connections
                    WHERE
                        provider =
                        'espn'::fantasy_provider
                    ORDER BY id
                    LIMIT 1
                ),
                provider_season_id = year::text,
                status = 'active'::season_status,
                team_count = (
                    SELECT count(*)
                    FROM teams
                    WHERE
                        teams.season_id =
                        seasons.id
                ),
                supports_head_to_head = true,
                supports_weekly_matchups = true,
                supports_lineup_history = true,
                supports_optimal_lineups = true,
                supports_schedule_swap = true,
                supports_all_play = true,
                supports_league_average = true,
                supports_team_logos = false,
                activated_at = created_at
            WHERE provider_connection_id IS NULL
            """
        )
    )


def downgrade() -> None:
    """Remove the canonical platform foundation."""

    op.drop_constraint(
        ("fk_seasons_provider_connection_id_provider_connections"),
        "seasons",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_seasons_status",
        table_name="seasons",
    )
    op.drop_index(
        "ix_seasons_provider_connection_id",
        table_name="seasons",
    )

    for column_name in (
        "archived_at",
        "completed_at",
        "activated_at",
        "configuration_snapshot",
        "supports_team_logos",
        "supports_league_average",
        "supports_all_play",
        "supports_schedule_swap",
        "supports_optimal_lineups",
        "supports_lineup_history",
        "supports_weekly_matchups",
        "supports_head_to_head",
        "scoring_type",
        "playoff_week_end",
        "playoff_week_start",
        "team_count",
        "status",
        "provider_season_id",
        "provider_connection_id",
    ):
        op.drop_column(
            "seasons",
            column_name,
        )

    op.drop_constraint(
        "fk_leagues_installation_id_installations",
        "leagues",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_leagues_installation_id",
        table_name="leagues",
    )

    for column_name in (
        "logo_url",
        "sport",
        "provider_league_id",
        "provider",
        "installation_id",
    ):
        op.drop_column(
            "leagues",
            column_name,
        )

    op.drop_index(
        "ix_provider_connections_provider",
        table_name="provider_connections",
    )
    op.drop_index(
        "ix_provider_connections_is_active",
        table_name="provider_connections",
    )
    op.drop_index(
        "ix_provider_connections_installation_id",
        table_name="provider_connections",
    )
    op.drop_table("provider_connections")
    op.drop_table("installations")

    bind = op.get_bind()

    season_status.drop(
        bind,
        checkfirst=True,
    )
    fantasy_sport.drop(
        bind,
        checkfirst=True,
    )
    fantasy_provider.drop(
        bind,
        checkfirst=True,
    )
    installation_status.drop(
        bind,
        checkfirst=True,
    )
