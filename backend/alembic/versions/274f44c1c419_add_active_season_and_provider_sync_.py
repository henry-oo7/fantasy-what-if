"""Add active season and provider synchronization metadata.

Revision ID: 274f44c1c419
Revises: 71c655604e85
Create Date: 2026-11-08 02:00:05.597566
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "274f44c1c419"
down_revision: str | Sequence[str] | None = "71c655604e85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


fantasy_provider = postgresql.ENUM(
    "espn",
    "sleeper",
    "yahoo",
    name="fantasy_provider",
    create_type=False,
)


def upgrade() -> None:
    """Add active installation pointers and sync metadata."""

    op.add_column(
        "installations",
        sa.Column(
            "active_league_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "installations",
        sa.Column(
            "active_season_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        ("fk_installations_active_league_id_leagues"),
        "installations",
        "leagues",
        ["active_league_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        ("fk_installations_active_season_id_seasons"),
        "installations",
        "seasons",
        ["active_season_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "uq_seasons_one_active_per_league",
        "seasons",
        ["league_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'::season_status"),
    )

    op.add_column(
        "sync_runs",
        sa.Column(
            "provider_connection_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "sync_runs",
        sa.Column(
            "provider",
            fantasy_provider,
            nullable=True,
        ),
    )
    op.add_column(
        "sync_runs",
        sa.Column(
            "provider_request_metadata",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "sync_runs",
        sa.Column(
            "analytics_rebuilt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_index(
        "ix_sync_runs_provider",
        "sync_runs",
        ["provider"],
    )
    op.create_index(
        "ix_sync_runs_provider_connection_id",
        "sync_runs",
        ["provider_connection_id"],
    )
    op.create_foreign_key(
        ("fk_sync_runs_provider_connection_id_provider_connections"),
        "sync_runs",
        "provider_connections",
        ["provider_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Point each migrated installation to its active league and season.
    op.execute(
        sa.text(
            """
            UPDATE installations AS installation
            SET
                active_league_id = active.league_id,
                active_season_id = active.season_id
            FROM (
                SELECT
                    league.installation_id,
                    league.id AS league_id,
                    season.id AS season_id
                FROM leagues AS league
                JOIN seasons AS season
                  ON season.league_id = league.id
                WHERE
                    season.status =
                    'active'::season_status
            ) AS active
            WHERE
                installation.id =
                active.installation_id
            """
        )
    )

    # Associate preserved synchronization history with the canonical
    # provider connection. Historical runs did not rebuild persisted
    # analytics, so analytics_rebuilt remains false.
    op.execute(
        sa.text(
            """
            UPDATE sync_runs AS sync_run
            SET
                provider_connection_id =
                    season.provider_connection_id,
                provider =
                    league.provider,
                analytics_rebuilt = false
            FROM seasons AS season
            JOIN leagues AS league
              ON league.id = season.league_id
            WHERE
                sync_run.season_id = season.id
                AND (
                    sync_run.provider_connection_id
                        IS NULL
                    OR sync_run.provider IS NULL
                )
            """
        )
    )

    # A failed sync may predate season assignment. Associate any
    # remaining preserved runs with the migrated ESPN connection while
    # leaving season_id unchanged.
    op.execute(
        sa.text(
            """
            UPDATE sync_runs
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
                provider =
                    'espn'::fantasy_provider,
                analytics_rebuilt = false
            WHERE provider IS NULL
            """
        )
    )


def downgrade() -> None:
    """Remove active pointers and provider sync metadata."""

    op.drop_constraint(
        ("fk_sync_runs_provider_connection_id_provider_connections"),
        "sync_runs",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_sync_runs_provider_connection_id",
        table_name="sync_runs",
    )
    op.drop_index(
        "ix_sync_runs_provider",
        table_name="sync_runs",
    )
    op.drop_column(
        "sync_runs",
        "analytics_rebuilt",
    )
    op.drop_column(
        "sync_runs",
        "provider_request_metadata",
    )
    op.drop_column(
        "sync_runs",
        "provider",
    )
    op.drop_column(
        "sync_runs",
        "provider_connection_id",
    )

    op.drop_index(
        "uq_seasons_one_active_per_league",
        table_name="seasons",
        postgresql_where=sa.text("status = 'active'::season_status"),
    )

    op.drop_constraint(
        ("fk_installations_active_season_id_seasons"),
        "installations",
        type_="foreignkey",
    )
    op.drop_constraint(
        ("fk_installations_active_league_id_leagues"),
        "installations",
        type_="foreignkey",
    )
    op.drop_column(
        "installations",
        "active_season_id",
    )
    op.drop_column(
        "installations",
        "active_league_id",
    )
