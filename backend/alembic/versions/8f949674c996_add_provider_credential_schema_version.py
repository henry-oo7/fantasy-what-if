"""Add provider credential schema version.

Revision ID: 8f949674c996
Revises: db0801db5da3
Create Date: 2026-11-19 21:52:03.887959
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8f949674c996"
down_revision: str | Sequence[str] | None = "db0801db5da3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add explicit credential payload schema metadata."""

    op.add_column(
        "provider_connections",
        sa.Column(
            "credential_schema_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_provider_connections_credential_schema_version_positive",
        "provider_connections",
        "credential_schema_version > 0",
    )


def downgrade() -> None:
    """Remove explicit credential payload schema metadata."""

    op.drop_constraint(
        "ck_provider_connections_credential_schema_version_positive",
        "provider_connections",
        type_="check",
    )
    op.drop_column(
        "provider_connections",
        "credential_schema_version",
    )
