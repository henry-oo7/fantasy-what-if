"""Add session revocation reason.

Revision ID: 346c51329ed8
Revises: 85a6c9d912f4

Existing revoked sessions keep a NULL reason. A NULL reason is always
treated as a generic unauthenticated session, never as ``replaced``.
No backfill infers a reason from timestamps or other indirect state.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "346c51329ed8"
down_revision: str | Sequence[str] | None = "85a6c9d912f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


session_revocation_reason = postgresql.ENUM(
    "replaced",
    "logout",
    "password_reset",
    "spectator_password_reset",
    "spectator_disabled",
    name="session_revocation_reason",
    create_type=False,
)

CHECK_CONSTRAINT_NAME = "ck_auth_sessions_revocation_reason_requires_revoked_at"


def upgrade() -> None:
    """Persist why each authentication session was revoked."""

    session_revocation_reason.create(
        op.get_bind(),
        checkfirst=False,
    )

    op.add_column(
        "auth_sessions",
        sa.Column(
            "revocation_reason",
            session_revocation_reason,
            nullable=True,
        ),
    )

    op.create_check_constraint(
        CHECK_CONSTRAINT_NAME,
        "auth_sessions",
        "revocation_reason IS NULL OR revoked_at IS NOT NULL",
    )


def downgrade() -> None:
    """Remove session revocation reasons."""

    op.drop_constraint(
        CHECK_CONSTRAINT_NAME,
        "auth_sessions",
        type_="check",
    )
    op.drop_column(
        "auth_sessions",
        "revocation_reason",
    )

    session_revocation_reason.drop(
        op.get_bind(),
        checkfirst=False,
    )
