"""Link account tokens to the transactional-email delivery attempt (ADR 0009).

Revision ID: a3c9e5f1b7d2
Revises: d7f2b9a4c6e1

Adds a nullable ``transactional_email_delivery_id`` and ``delivery_attempt``
pair to ``email_verification_tokens`` and ``password_reset_tokens``. A token
issued by the delivery worker records the delivery and the persisted attempt
that issued it; tokens from the current synchronous routes keep both NULL.
Existing rows are not backfilled.

The foreign key cascades: deleting a delivery deletes the tokens it issued.
SET NULL is not used because nulling only the delivery ID would violate the
pairing constraint.

Downgrade refuses while any token row has either new column populated, and
otherwise removes the columns and leaves legacy token rows intact.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3c9e5f1b7d2"
down_revision: str | Sequence[str] | None = "d7f2b9a4c6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOKEN_TABLES = ("email_verification_tokens", "password_reset_tokens")


def _pair_check() -> str:
    # IS [NOT] NULL never evaluates to SQL NULL, so a partial pair cannot pass.
    return (
        "(transactional_email_delivery_id IS NULL AND delivery_attempt IS NULL) "
        "OR (transactional_email_delivery_id IS NOT NULL AND delivery_attempt IS NOT NULL)"
    )


def upgrade() -> None:
    for table in TOKEN_TABLES:
        op.add_column(
            table,
            sa.Column("transactional_email_delivery_id", sa.Integer(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("delivery_attempt", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table}_transactional_email_delivery_id",
            table,
            "transactional_email_deliveries",
            ["transactional_email_delivery_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_check_constraint(
            f"ck_{table}_delivery_attempt_pair",
            table,
            _pair_check(),
        )
        op.create_check_constraint(
            f"ck_{table}_delivery_attempt_positive",
            table,
            "delivery_attempt IS NULL OR delivery_attempt > 0",
        )
        op.create_index(
            f"ix_{table}_user_delivery",
            table,
            ["user_id", "transactional_email_delivery_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    for table in TOKEN_TABLES:
        count = bind.execute(
            sa.text(
                f"SELECT count(*) FROM {table} "
                "WHERE transactional_email_delivery_id IS NOT NULL "
                "OR delivery_attempt IS NOT NULL"
            )
        ).scalar_one()

        if count:
            raise RuntimeError(
                f"Refusing to downgrade: {count} {table} row(s) are linked to a "
                "transactional-email delivery. Remove delivery-issued tokens explicitly first."
            )

    for table in TOKEN_TABLES:
        op.drop_index(f"ix_{table}_user_delivery", table_name=table)
        op.drop_constraint(f"ck_{table}_delivery_attempt_positive", table, type_="check")
        op.drop_constraint(f"ck_{table}_delivery_attempt_pair", table, type_="check")
        op.drop_constraint(f"fk_{table}_transactional_email_delivery_id", table, type_="foreignkey")
        op.drop_column(table, "delivery_attempt")
        op.drop_column(table, "transactional_email_delivery_id")
