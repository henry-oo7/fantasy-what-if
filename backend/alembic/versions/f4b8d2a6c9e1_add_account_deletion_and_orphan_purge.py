"""Add personal account deletion completion and orphan League purge (ADR 0010).

Revision ID: f4b8d2a6c9e1
Revises: e8c3a6d1f4b7

Adds ``account_deletion_completed`` to ``transactional_email_event_type`` and
``orphan_league_purge`` to ``background_job_type``.

- ``ck_transactional_email_deliveries_scope_shape`` admits the personal
  completion event only as the unscoped, user-less, encrypted-recipient row
  (the same shape as the workspace completion notice), and forbids it on a
  workspace-scoped delivery.
- A purge job may have a NULL ``workspace_id``. Its payload must be exactly
  ``{"version": 1, "league_id": <positive integer>}`` and its idempotency key
  ``orphan-league-purge:{league_id}``, so the existing partial unique index
  allows at most one active purge job per League.

``ALTER TYPE ... ADD VALUE`` runs inside the migration transaction. The new
values are never cast before commit: every CHECK compares ``::text``.

Downgrade refuses while any purge job or personal completion delivery exists.
When safe it restores the previous constraints and recreates both enum types
without the new values (PostgreSQL cannot drop an enum value). Nothing is
rewritten or deleted.
"""

# ruff: noqa: E501  (constraint SQL is kept as single literal strings)

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4b8d2a6c9e1"
down_revision: str | Sequence[str] | None = "e8c3a6d1f4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS_JOB_TYPES = (
    "initial_workspace_sync",
    "incremental_season_sync",
    "full_season_sync",
    "historical_season_import",
    "provider_credential_validation",
    "provider_token_refresh",
    "provisional_workspace_cleanup",
    "expired_session_cleanup",
    "expired_token_cleanup",
    "workspace_deletion",
    "transactional_email_delivery",
    "league_season_sync",
    "access_reverification",
)
PREVIOUS_EVENT_TYPES = (
    "administrator_email_verification",
    "administrator_password_reset",
    "administrator_password_changed",
    "workspace_deletion_requested",
    "workspace_deletion_reminder",
    "workspace_deletion_completed",
    "workspace_deletion_canceled",
)

WORKSPACE_CHECK = "ck_background_jobs_workspace_required_except_email"
PREVIOUS_WORKSPACE_SQL = (
    "workspace_id IS NOT NULL OR job_type::text IN "
    "('transactional_email_delivery', 'league_season_sync', 'access_reverification')"
)
NEW_WORKSPACE_SQL = (
    "workspace_id IS NOT NULL OR job_type::text IN "
    "('transactional_email_delivery', 'league_season_sync', 'access_reverification', 'orphan_league_purge')"
)

PURGE_CHECKS = (
    (
        "ck_background_jobs_orphan_league_purge_payload_shape",
        "job_type::text <> 'orphan_league_purge' OR COALESCE(payload_version = 1 AND jsonb_typeof(payload) = 'object' AND (payload - 'version' - 'league_id') = '{}'::jsonb AND payload ->> 'version' = '1' AND jsonb_typeof(payload -> 'league_id') = 'number' AND payload ->> 'league_id' ~ '^[1-9][0-9]{0,9}$', false)",
    ),
    (
        "ck_background_jobs_orphan_league_purge_key",
        "job_type::text <> 'orphan_league_purge' OR COALESCE(idempotency_key = 'orphan-league-purge:' || (payload ->> 'league_id'), false)",
    ),
)

# Checks that compare job_type are dropped and recreated around the enum retype.
RETAINED_JOB_CHECKS = (
    (
        "ck_background_jobs_transactional_email_payload_shape",
        "job_type::text <> 'transactional_email_delivery' OR COALESCE(jsonb_typeof(payload) = 'object' AND (payload - 'version' - 'delivery_id') = '{}'::jsonb AND payload ? 'version' AND payload ? 'delivery_id' AND jsonb_typeof(payload -> 'version') = 'number' AND jsonb_typeof(payload -> 'delivery_id') = 'number', false)",
    ),
    (
        "ck_background_jobs_league_season_sync_payload_shape",
        "job_type::text <> 'league_season_sync' OR COALESCE(payload_version = 1 AND jsonb_typeof(payload) = 'object' AND (payload - 'version' - 'season_id') = '{}'::jsonb AND jsonb_typeof(payload -> 'version') = 'number' AND payload ->> 'version' = '1' AND jsonb_typeof(payload -> 'season_id') = 'number' AND payload ->> 'season_id' ~ '^[1-9][0-9]{0,9}$', false)",
    ),
    (
        "ck_background_jobs_league_season_sync_key",
        "job_type::text <> 'league_season_sync' OR COALESCE(idempotency_key = 'league-season-sync:' || (payload ->> 'season_id'), false)",
    ),
    (
        "ck_background_jobs_access_reverification_payload_shape",
        "job_type::text <> 'access_reverification' OR COALESCE(payload_version = 1 AND jsonb_typeof(payload) = 'object' AND (payload - 'version' - 'user_id' - 'season_id') = '{}'::jsonb AND payload ->> 'version' = '1' AND jsonb_typeof(payload -> 'user_id') = 'number' AND payload ->> 'user_id' ~ '^[1-9][0-9]{0,9}$' AND jsonb_typeof(payload -> 'season_id') = 'number' AND payload ->> 'season_id' ~ '^[1-9][0-9]{0,9}$', false)",
    ),
    (
        "ck_background_jobs_access_reverification_key",
        "job_type::text <> 'access_reverification' OR COALESCE(idempotency_key = 'access-reverification:' || (payload ->> 'user_id') || ':' || (payload ->> 'season_id'), false)",
    ),
)

SCOPE_CHECK = "ck_transactional_email_deliveries_scope_shape"
IDENTITY_USER_CHECK = "ck_transactional_email_deliveries_identity_event_user"
IDENTITY_USER_SQL = (
    "CASE WHEN event_type IN ('administrator_email_verification', 'administrator_password_reset', 'administrator_password_changed') THEN user_id IS NOT NULL ELSE true END"
)
_NONTERMINAL = "status IN ('pending', 'sending', 'retry_scheduled')"
_IDENTITY_BRANCH = (
    "OR (user_id IS NOT NULL "
    "AND event_type IN ('administrator_email_verification', 'administrator_password_reset', 'administrator_password_changed') "
    "AND encrypted_recipient IS NULL "
    "AND recipient_key_version IS NULL)) "
    "ELSE false END"
)
PREVIOUS_SCOPE_SQL = (
    "CASE "
    "WHEN scope = 'workspace' THEN workspace_id IS NOT NULL AND encrypted_recipient IS NULL AND recipient_key_version IS NULL AND event_type <> 'workspace_deletion_completed' "
    "WHEN scope = 'unscoped' THEN workspace_id IS NULL AND ("
    "(user_id IS NULL AND event_type = 'workspace_deletion_completed' "
    f"AND (({_NONTERMINAL}) = (encrypted_recipient IS NOT NULL))) " + _IDENTITY_BRANCH
)
NEW_SCOPE_SQL = (
    "CASE "
    "WHEN scope = 'workspace' THEN workspace_id IS NOT NULL AND encrypted_recipient IS NULL AND recipient_key_version IS NULL AND event_type::text NOT IN ('workspace_deletion_completed', 'account_deletion_completed') "
    "WHEN scope = 'unscoped' THEN workspace_id IS NULL AND ("
    "(user_id IS NULL AND event_type::text IN ('workspace_deletion_completed', 'account_deletion_completed') "
    f"AND (({_NONTERMINAL}) = (encrypted_recipient IS NOT NULL))) " + _IDENTITY_BRANCH
)


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE transactional_email_event_type ADD VALUE 'account_deletion_completed'"))
    op.execute(sa.text("ALTER TYPE background_job_type ADD VALUE 'orphan_league_purge'"))

    op.drop_constraint(SCOPE_CHECK, "transactional_email_deliveries", type_="check")
    op.create_check_constraint(SCOPE_CHECK, "transactional_email_deliveries", NEW_SCOPE_SQL)

    op.drop_constraint(WORKSPACE_CHECK, "background_jobs", type_="check")
    op.create_check_constraint(WORKSPACE_CHECK, "background_jobs", NEW_WORKSPACE_SQL)

    for name, sql in PURGE_CHECKS:
        op.create_check_constraint(name, "background_jobs", sql)


def downgrade() -> None:
    bind = op.get_bind()

    purge_jobs = bind.execute(
        sa.text("SELECT count(*) FROM background_jobs WHERE job_type::text = 'orphan_league_purge'")
    ).scalar_one()
    completions = bind.execute(
        sa.text(
            "SELECT count(*) FROM transactional_email_deliveries "
            "WHERE event_type::text = 'account_deletion_completed'"
        )
    ).scalar_one()

    if purge_jobs:
        raise RuntimeError(
            f"Refusing to downgrade: {purge_jobs} orphan_league_purge background job(s) exist. "
            "Remove the purge job state explicitly first."
        )

    if completions:
        raise RuntimeError(
            f"Refusing to downgrade: {completions} account-deletion completion delivery(ies) exist. "
            "Remove the personal deletion email state explicitly first."
        )

    for name, _sql in reversed(PURGE_CHECKS):
        op.drop_constraint(name, "background_jobs", type_="check")

    for name, _sql in RETAINED_JOB_CHECKS:
        op.drop_constraint(name, "background_jobs", type_="check")

    op.drop_constraint(WORKSPACE_CHECK, "background_jobs", type_="check")

    op.drop_constraint(SCOPE_CHECK, "transactional_email_deliveries", type_="check")
    op.drop_constraint(IDENTITY_USER_CHECK, "transactional_email_deliveries", type_="check")

    op.execute(sa.text("ALTER TYPE background_job_type RENAME TO background_job_type_old"))
    job_values = ", ".join(f"'{value}'" for value in PREVIOUS_JOB_TYPES)
    op.execute(sa.text(f"CREATE TYPE background_job_type AS ENUM ({job_values})"))
    op.execute(
        sa.text(
            "ALTER TABLE background_jobs ALTER COLUMN job_type "
            "TYPE background_job_type USING job_type::text::background_job_type"
        )
    )
    op.execute(sa.text("DROP TYPE background_job_type_old"))

    op.execute(sa.text("ALTER TYPE transactional_email_event_type RENAME TO transactional_email_event_type_old"))
    event_values = ", ".join(f"'{value}'" for value in PREVIOUS_EVENT_TYPES)
    op.execute(sa.text(f"CREATE TYPE transactional_email_event_type AS ENUM ({event_values})"))
    op.execute(
        sa.text(
            "ALTER TABLE transactional_email_deliveries ALTER COLUMN event_type "
            "TYPE transactional_email_event_type USING event_type::text::transactional_email_event_type"
        )
    )
    op.execute(sa.text("DROP TYPE transactional_email_event_type_old"))

    for name, sql in RETAINED_JOB_CHECKS:
        op.create_check_constraint(name, "background_jobs", sql)

    op.create_check_constraint(WORKSPACE_CHECK, "background_jobs", PREVIOUS_WORKSPACE_SQL)
    op.create_check_constraint(IDENTITY_USER_CHECK, "transactional_email_deliveries", IDENTITY_USER_SQL)
    op.create_check_constraint(SCOPE_CHECK, "transactional_email_deliveries", PREVIOUS_SCOPE_SQL)
