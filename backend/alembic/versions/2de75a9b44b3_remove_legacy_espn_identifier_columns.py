"""Remove legacy ESPN identifier columns.

Revision ID: 2de75a9b44b3
Revises: a5f003058442
Create Date: 2026-11-08 22:56:14.459600
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2de75a9b44b3"
down_revision: str | Sequence[str] | None = "a5f003058442"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove redundant ESPN-specific identifiers."""

    op.drop_index(
        "ix_leagues_espn_league_id",
        table_name="leagues",
    )

    op.drop_constraint(
        "uq_teams_season_espn_team",
        "teams",
        type_="unique",
    )

    op.drop_column(
        "player_week_scores",
        "espn_player_id",
    )
    op.drop_column(
        "teams",
        "espn_team_id",
    )
    op.drop_column(
        "leagues",
        "espn_league_id",
    )


def downgrade() -> None:
    """Restore legacy ESPN identifiers from canonical provider IDs.

    Only ESPN rows receive reconstructed legacy identifiers.
    Non-ESPN rows remain null, matching the nullable A7 schema.

    The downgrade refuses to continue if an ESPN canonical identifier
    is missing, nonnumeric, outside the legacy integer range, or invalid
    for the historical column.
    """

    connection = op.get_bind()

    invalid_league_identifiers = connection.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM leagues
                WHERE
                    provider::text = 'espn'
                    AND (
                        provider_league_id IS NULL
                        OR provider_league_id
                            !~ '^[0-9]+$'
                        OR provider_league_id::numeric < 1
                        OR provider_league_id::numeric
                            > 9223372036854775807
                    )
            )
            """
        )
    ).scalar_one()

    invalid_team_identifiers = connection.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM teams AS team
                JOIN seasons AS season
                    ON season.id = team.season_id
                JOIN leagues AS league
                    ON league.id = season.league_id
                WHERE
                    league.provider::text = 'espn'
                    AND (
                        team.provider_team_id IS NULL
                        OR team.provider_team_id
                            !~ '^[0-9]+$'
                        OR team.provider_team_id::numeric < 1
                        OR team.provider_team_id::numeric
                            > 2147483647
                    )
            )
            """
        )
    ).scalar_one()

    invalid_player_identifiers = connection.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM player_week_scores AS player_score
                JOIN seasons AS season
                    ON season.id = player_score.season_id
                JOIN leagues AS league
                    ON league.id = season.league_id
                WHERE
                    league.provider::text = 'espn'
                    AND (
                        player_score.provider_player_id
                            IS NULL
                        OR player_score.provider_player_id
                            !~ '^-?[0-9]+$'
                        OR player_score.provider_player_id::numeric
                            < -9223372036854775808
                        OR player_score.provider_player_id::numeric
                            > 9223372036854775807
                    )
            )
            """
        )
    ).scalar_one()

    if invalid_league_identifiers:
        raise RuntimeError(
            "Cannot downgrade because an ESPN league has an invalid canonical provider_league_id."
        )

    if invalid_team_identifiers:
        raise RuntimeError(
            "Cannot downgrade because an ESPN team has an invalid canonical provider_team_id."
        )

    if invalid_player_identifiers:
        raise RuntimeError(
            "Cannot downgrade because an ESPN player-week row has an "
            "invalid canonical provider_player_id."
        )

    op.add_column(
        "leagues",
        sa.Column(
            "espn_league_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )

    op.add_column(
        "teams",
        sa.Column(
            "espn_team_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "player_week_scores",
        sa.Column(
            "espn_player_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE leagues
            SET espn_league_id
                = provider_league_id::bigint
            WHERE provider::text = 'espn'
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE teams AS team
            SET espn_team_id
                = team.provider_team_id::integer
            FROM seasons AS season,
                 leagues AS league
            WHERE
                season.id = team.season_id
                AND league.id = season.league_id
                AND league.provider::text = 'espn'
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE player_week_scores AS player_score
            SET espn_player_id
                = player_score.provider_player_id::bigint
            FROM seasons AS season,
                 leagues AS league
            WHERE
                season.id = player_score.season_id
                AND league.id = season.league_id
                AND league.provider::text = 'espn'
            """
        )
    )

    op.create_index(
        "ix_leagues_espn_league_id",
        "leagues",
        ["espn_league_id"],
        unique=True,
    )

    op.create_unique_constraint(
        "uq_teams_season_espn_team",
        "teams",
        [
            "season_id",
            "espn_team_id",
        ],
    )
