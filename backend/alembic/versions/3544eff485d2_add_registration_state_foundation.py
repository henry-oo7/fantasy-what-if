"""Add SaaS registration state foundation.

Revision ID: 3544eff485d2
Revises: 8f949674c996
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "3544eff485d2"
down_revision: str | Sequence[str] | None = "8f949674c996"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


workspace_registration_status = postgresql.ENUM(
    "provisional",
    "active",
    "suspended",
    "deletion_pending",
    "deleted",
    name="workspace_registration_status",
    create_type=False,
)

onboarding_stage = postgresql.ENUM(
    "registration_pending",
    "email_verification_pending",
    "provider_setup_pending",
    "league_claim_pending",
    "initial_sync_pending",
    "initial_sync_running",
    "spectator_setup_pending",
    "complete",
    "provider_validation_failed",
    "league_claim_conflict",
    "initial_sync_failed",
    name="onboarding_stage",
    create_type=False,
)


def upgrade() -> None:
    """Add provisional workspace and account-token persistence."""

    bind = op.get_bind()

    workspace_registration_status.create(
        bind,
        checkfirst=True,
    )
    onboarding_stage.create(
        bind,
        checkfirst=True,
    )

    op.add_column(
        "installations",
        sa.Column(
            "registration_status",
            workspace_registration_status,
            nullable=True,
        ),
    )
    op.add_column(
        "installations",
        sa.Column(
            "onboarding_stage",
            onboarding_stage,
            nullable=True,
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE installations
            SET registration_status =
                'active'::workspace_registration_status
            WHERE status = 'active'::installation_status
            """
        )
    )

    op.create_index(
        "ix_installations_registration_status",
        "installations",
        ["registration_status"],
        unique=False,
    )
    op.create_index(
        "ix_installations_onboarding_stage",
        "installations",
        ["onboarding_stage"],
        unique=False,
    )

    _create_token_table(
        table_name="email_verification_tokens",
        foreign_key_name=("fk_email_verification_tokens_user_id_users"),
        unique_constraint_name=("uq_email_verification_tokens_token_hash"),
    )
    _create_token_indexes(
        table_name="email_verification_tokens",
    )

    _create_token_table(
        table_name="password_reset_tokens",
        foreign_key_name=("fk_password_reset_tokens_user_id_users"),
        unique_constraint_name=("uq_password_reset_tokens_token_hash"),
    )
    _create_token_indexes(
        table_name="password_reset_tokens",
    )


def downgrade() -> None:
    """Remove provisional workspace and account-token persistence."""

    _drop_token_indexes(
        table_name="password_reset_tokens",
    )
    op.drop_table("password_reset_tokens")

    _drop_token_indexes(
        table_name="email_verification_tokens",
    )
    op.drop_table("email_verification_tokens")

    op.drop_index(
        "ix_installations_onboarding_stage",
        table_name="installations",
    )
    op.drop_index(
        "ix_installations_registration_status",
        table_name="installations",
    )
    op.drop_column(
        "installations",
        "onboarding_stage",
    )
    op.drop_column(
        "installations",
        "registration_status",
    )

    onboarding_stage.drop(
        op.get_bind(),
        checkfirst=True,
    )
    workspace_registration_status.drop(
        op.get_bind(),
        checkfirst=True,
    )


def _create_token_table(
    *,
    table_name: str,
    foreign_key_name: str,
    unique_constraint_name: str,
) -> None:
    op.create_table(
        table_name,
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "token_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=foreign_key_name,
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "token_hash",
            name=unique_constraint_name,
        ),
    )


def _create_token_indexes(
    *,
    table_name: str,
) -> None:
    for column_name in (
        "user_id",
        "expires_at",
        "used_at",
    ):
        op.create_index(
            f"ix_{table_name}_{column_name}",
            table_name,
            [column_name],
            unique=False,
        )


def _drop_token_indexes(
    *,
    table_name: str,
) -> None:
    for column_name in (
        "used_at",
        "expires_at",
        "user_id",
    ):
        op.drop_index(
            f"ix_{table_name}_{column_name}",
            table_name=table_name,
        )
