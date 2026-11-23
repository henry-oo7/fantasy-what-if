"""Add validated provider target and access mode to provider connections.

Revision ID: b7d41e9c2a10
Revises: 346c51329ed8

SaaS onboarding validates an ESPN league target before the authoritative
league claim. A public target has no credentials, so the target is stored
in plain columns and the credential key version becomes nullable.

Existing rows stay legacy: access mode and target fields remain NULL and
no row is classified as public or private. The upgrade refuses to run if
any installation has more than one active connection or if credential
state is not one of the two known legacy shapes. The downgrade refuses
to run once any new-format row exists, because the old schema cannot
represent a public connection or the validated target.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7d41e9c2a10"
down_revision: str | Sequence[str] | None = "346c51329ed8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "provider_connections"
LEGACY_PLACEHOLDER_KEY_VERSION = 1

provider_access_mode = postgresql.ENUM(
    "public",
    "private",
    name="provider_access_mode",
    create_type=False,
)

CREDENTIAL_PAIR = "ck_provider_connections_credential_pair"
ACCESS_MODE_STATE = "ck_provider_connections_access_mode_state"
SEASON_YEAR_RANGE = "ck_provider_connections_season_year_range"
ACTIVE_UNIQUE_INDEX = "uq_provider_connections_active_installation"


def _abort_if(sql: str, message: str) -> None:
    count = op.get_bind().execute(sa.text(sql)).scalar_one()

    if count:
        raise RuntimeError(f"{message} ({count} found). No changes were made.")


def upgrade() -> None:
    """Represent public and private validated targets without fake credentials."""

    _abort_if(
        f"""
        SELECT count(*) FROM (
            SELECT installation_id FROM {TABLE}
            WHERE is_active IS TRUE
            GROUP BY installation_id
            HAVING count(*) > 1
        ) AS duplicates
        """,
        "Installations with more than one active provider connection must be "
        "resolved before this migration",
    )
    _abort_if(
        f"""
        SELECT count(*) FROM {TABLE}
        WHERE NOT (
            (encrypted_credentials IS NOT NULL AND credential_version IS NOT NULL)
            OR (
                encrypted_credentials IS NULL
                AND credential_version = {LEGACY_PLACEHOLDER_KEY_VERSION}
            )
        )
        """,
        "Provider connections with unexpected credential state must be "
        "reviewed before this migration",
    )

    provider_access_mode.create(op.get_bind(), checkfirst=False)

    op.add_column(TABLE, sa.Column("provider_league_id", sa.String(32), nullable=True))
    op.add_column(TABLE, sa.Column("provider_season_year", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("access_mode", provider_access_mode, nullable=True))

    op.alter_column(
        TABLE,
        "credential_version",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
        existing_server_default=sa.text("1"),
    )

    # The legacy placeholder: no ciphertext with the old default key version.
    op.execute(
        f"UPDATE {TABLE} SET credential_version = NULL WHERE encrypted_credentials IS NULL"
    )

    op.create_check_constraint(
        CREDENTIAL_PAIR,
        TABLE,
        "(encrypted_credentials IS NULL) = (credential_version IS NULL)",
    )
    # Each branch tests access_mode IS NOT NULL explicitly: a NULL comparison
    # would otherwise make the whole check NULL, which PostgreSQL accepts.
    op.create_check_constraint(
        ACCESS_MODE_STATE,
        TABLE,
        """
        (
            access_mode IS NULL
            AND provider_league_id IS NULL
            AND provider_season_year IS NULL
        )
        OR (
            access_mode IS NOT NULL
            AND access_mode = 'public'
            AND provider_league_id IS NOT NULL
            AND provider_season_year IS NOT NULL
            AND encrypted_credentials IS NULL
        )
        OR (
            access_mode IS NOT NULL
            AND access_mode = 'private'
            AND provider_league_id IS NOT NULL
            AND provider_season_year IS NOT NULL
            AND encrypted_credentials IS NOT NULL
        )
        """,
    )
    op.create_check_constraint(
        SEASON_YEAR_RANGE,
        TABLE,
        "provider_season_year IS NULL OR provider_season_year BETWEEN 2000 AND 2100",
    )
    op.create_index(
        ACTIVE_UNIQUE_INDEX,
        TABLE,
        ["installation_id"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )


def downgrade() -> None:
    """Remove validated targets; refuse while new-format rows exist."""

    _abort_if(
        f"""
        SELECT count(*) FROM {TABLE}
        WHERE access_mode IS NOT NULL
           OR provider_league_id IS NOT NULL
           OR provider_season_year IS NOT NULL
        """,
        "Provider connections with a validated public or private target cannot "
        "be represented by the previous schema; clean up or migrate them "
        "deliberately before downgrading",
    )

    op.drop_index(ACTIVE_UNIQUE_INDEX, table_name=TABLE)
    op.drop_constraint(SEASON_YEAR_RANGE, TABLE, type_="check")
    op.drop_constraint(ACCESS_MODE_STATE, TABLE, type_="check")
    op.drop_constraint(CREDENTIAL_PAIR, TABLE, type_="check")

    # Restore the representable legacy placeholder for rows without ciphertext.
    op.execute(
        f"UPDATE {TABLE} SET credential_version = {LEGACY_PLACEHOLDER_KEY_VERSION} "
        "WHERE credential_version IS NULL AND encrypted_credentials IS NULL"
    )
    op.alter_column(
        TABLE,
        "credential_version",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
    )

    op.drop_column(TABLE, "access_mode")
    op.drop_column(TABLE, "provider_season_year")
    op.drop_column(TABLE, "provider_league_id")

    provider_access_mode.drop(op.get_bind(), checkfirst=False)
