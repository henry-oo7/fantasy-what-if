"""add provider league claim foundation

Revision ID: 9d5b27f1a1df
Revises: 786913fdec15
Create Date: 2026-11-18 21:55:50.993214
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9d5b27f1a1df"
down_revision: str | Sequence[str] | None = "786913fdec15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add globally unique provider-league ownership claims."""

    connection = op.get_bind()

    invalid_existing_leagues = int(
        connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM leagues
                WHERE installation_id IS NULL
                   OR provider IS NULL
                   OR provider_league_id IS NULL
                   OR btrim(provider_league_id) = ''
                """
            )
        )
        or 0
    )

    if invalid_existing_leagues:
        raise RuntimeError(
            "Provider league claim migration requires every "
            "existing league to have a workspace, provider, "
            "and provider league ID."
        )

    op.create_table(
        "provider_league_claims",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "league_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "provider",
            sa.Enum(
                "espn",
                "sleeper",
                "yahoo",
                name="fantasy_provider",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "provider_league_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "normalized_provider_league_id",
            sa.String(length=255),
            nullable=False,
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
            "provider_league_id = btrim(provider_league_id)",
            name="ck_provider_league_claims_trimmed_id",
        ),
        sa.CheckConstraint(
            "normalized_provider_league_id = btrim(provider_league_id)",
            name="ck_provider_league_claims_normalized_id",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["installations.id"],
            name=("fk_provider_league_claims_workspace_id_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name=("fk_provider_league_claims_league_id_leagues"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            name="uq_provider_league_claims_workspace_id",
        ),
        sa.UniqueConstraint(
            "league_id",
            name="uq_provider_league_claims_league_id",
        ),
        sa.UniqueConstraint(
            "provider",
            "normalized_provider_league_id",
            name="uq_provider_league_claims_provider_identity",
        ),
    )

    op.create_index(
        "ix_provider_league_claims_provider",
        "provider_league_claims",
        ["provider"],
        unique=False,
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO provider_league_claims (
                workspace_id,
                league_id,
                provider,
                provider_league_id,
                normalized_provider_league_id
            )
            SELECT
                installation_id,
                id,
                provider,
                btrim(provider_league_id),
                btrim(provider_league_id)
            FROM leagues
            ORDER BY id
            """
        )
    )


def downgrade() -> None:
    """Remove provider-league ownership claims."""

    op.drop_index(
        "ix_provider_league_claims_provider",
        table_name="provider_league_claims",
    )
    op.drop_table("provider_league_claims")
