"""Add workspace-free personal account identity (ADR 0010).

Revision ID: d3a7e5b1c9f2
Revises: b9e4c2d7a1f3

Expand step only. Nothing is dropped.

- ``users.role`` and ``users.username`` become nullable and ``role`` loses its
  ``'spectator'`` server default, so a personal account never silently
  inherits a role. ``users.workspace_id`` was already nullable.
- ``ck_users_identity_shape`` admits exactly two shapes: a legacy row (role and
  username both present, any workspace) or a personal row (no workspace, role
  or username; a nonempty display name and one normalized email). It never
  reinterprets a spectator as a personal account.
- ``uq_users_admin_normalized_email`` (administrators only) is replaced by
  ``uq_users_normalized_email`` over every row with a normalized email, so an
  email maps to at most one login-capable user. Duplicates refuse the upgrade
  with a bounded message that names no email address and merges nothing.
- ``ck_transactional_email_deliveries_scope_shape`` additionally admits a
  user-owned identity delivery: ``unscoped``, no workspace, ``user_id`` set,
  an identity event, and no encrypted recipient. The completion notice and
  workspace-scoped rules are unchanged.

Downgrade refuses while any target-only state exists (a personal user, a user
without role or username, or a user-owned identity delivery). When safe it
restores the previous constraints. It never invents workspaces or usernames
and never deletes personal state.
"""

# ruff: noqa: E501  (constraint SQL is kept as single literal strings)

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3a7e5b1c9f2"
down_revision: str | Sequence[str] | None = "b9e4c2d7a1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IDENTITY_SHAPE_CHECK = "ck_users_identity_shape"
IDENTITY_SHAPE_SQL = (
    "(role IS NOT NULL AND username IS NOT NULL) OR "
    "(workspace_id IS NULL AND role IS NULL AND username IS NULL "
    "AND normalized_email IS NOT NULL AND email = normalized_email "
    "AND btrim(display_name) <> '')"
)

SCOPE_CHECK = "ck_transactional_email_deliveries_scope_shape"
_NONTERMINAL = "status IN ('pending', 'sending', 'retry_scheduled')"
_WORKSPACE_BRANCH = (
    "WHEN scope = 'workspace' THEN "
    "workspace_id IS NOT NULL "
    "AND encrypted_recipient IS NULL "
    "AND recipient_key_version IS NULL "
    "AND event_type <> 'workspace_deletion_completed' "
)
PREVIOUS_SCOPE_SQL = (
    "CASE "
    + _WORKSPACE_BRANCH
    + "WHEN scope = 'unscoped' THEN "
    "workspace_id IS NULL "
    "AND user_id IS NULL "
    "AND event_type = 'workspace_deletion_completed' "
    f"AND (({_NONTERMINAL}) = (encrypted_recipient IS NOT NULL)) "
    "ELSE false END"
)
NEW_SCOPE_SQL = (
    "CASE "
    + _WORKSPACE_BRANCH
    + "WHEN scope = 'unscoped' THEN workspace_id IS NULL AND ("
    "(user_id IS NULL "
    "AND event_type = 'workspace_deletion_completed' "
    f"AND (({_NONTERMINAL}) = (encrypted_recipient IS NOT NULL))) "
    "OR (user_id IS NOT NULL "
    "AND event_type IN ('administrator_email_verification', 'administrator_password_reset', 'administrator_password_changed') "
    "AND encrypted_recipient IS NULL "
    "AND recipient_key_version IS NULL)) "
    "ELSE false END"
)


def upgrade() -> None:
    bind = op.get_bind()

    duplicates = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT 1 FROM users "
            "WHERE normalized_email IS NOT NULL "
            "GROUP BY normalized_email HAVING count(*) > 1) AS duplicated"
        )
    ).scalar_one()

    if duplicates:
        raise RuntimeError(
            f"Refusing to upgrade: {duplicates} normalized email value(s) belong to more "
            "than one user. Resolve the duplicate accounts explicitly; none were merged."
        )

    op.alter_column("users", "role", server_default=None, nullable=True)
    op.alter_column("users", "username", nullable=True)
    op.create_check_constraint(IDENTITY_SHAPE_CHECK, "users", IDENTITY_SHAPE_SQL)

    op.drop_index("uq_users_admin_normalized_email", table_name="users")
    op.create_index(
        "uq_users_normalized_email",
        "users",
        ["normalized_email"],
        unique=True,
        postgresql_where=sa.text("normalized_email IS NOT NULL"),
    )

    op.drop_constraint(SCOPE_CHECK, "transactional_email_deliveries", type_="check")
    op.create_check_constraint(SCOPE_CHECK, "transactional_email_deliveries", NEW_SCOPE_SQL)


def _refuse_if_any(bind: sa.Connection, description: str, query: str) -> None:
    count = bind.execute(sa.text(query)).scalar_one()

    if count:
        raise RuntimeError(
            f"Refusing to downgrade: {count} {description}. "
            "Remove the personal-account state explicitly first."
        )


def downgrade() -> None:
    bind = op.get_bind()

    _refuse_if_any(
        bind,
        "user(s) have no role or username (personal accounts)",
        "SELECT count(*) FROM users WHERE role IS NULL OR username IS NULL",
    )
    _refuse_if_any(
        bind,
        "user-owned identity delivery(ies) have no workspace",
        "SELECT count(*) FROM transactional_email_deliveries "
        "WHERE workspace_id IS NULL AND user_id IS NOT NULL",
    )
    _refuse_if_any(
        bind,
        "delivery(ies) would violate the previous ownership constraint",
        f"SELECT count(*) FROM transactional_email_deliveries WHERE NOT ({PREVIOUS_SCOPE_SQL})",
    )

    op.drop_constraint(SCOPE_CHECK, "transactional_email_deliveries", type_="check")
    op.create_check_constraint(SCOPE_CHECK, "transactional_email_deliveries", PREVIOUS_SCOPE_SQL)

    op.drop_index("uq_users_normalized_email", table_name="users")
    op.create_index(
        "uq_users_admin_normalized_email",
        "users",
        ["normalized_email"],
        unique=True,
        postgresql_where=sa.text("role = 'admin'::user_role AND normalized_email IS NOT NULL"),
    )

    op.drop_constraint(IDENTITY_SHAPE_CHECK, "users", type_="check")
    op.alter_column("users", "username", nullable=False)
    op.alter_column(
        "users",
        "role",
        server_default=sa.text("'spectator'::user_role"),
        nullable=False,
    )
