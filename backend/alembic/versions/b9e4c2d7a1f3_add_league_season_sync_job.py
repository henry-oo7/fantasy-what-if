"""Add the League-Season synchronization background job (ADR 0010).

Revision ID: b9e4c2d7a1f3
Revises: f1b6d2c8a9e4

Adds ``league_season_sync`` to ``background_job_type``. A job of that type
may have a NULL ``workspace_id``; every other job except unscoped
transactional email still requires a workspace. Its payload must be exactly
``{"version": 1, "season_id": <positive integer>}`` and its idempotency key
must be ``league-season-sync:{season_id}``, so the existing partial unique
index allows at most one active synchronization job per Season.

``ALTER TYPE ... ADD VALUE`` runs inside the migration transaction. The new
value is never cast before commit: every CHECK compares ``job_type::text``.

Downgrade refuses while any Season-sync job or any non-email NULL-workspace
job exists. When safe, it restores the prior workspace CHECK and recreates
``background_job_type`` without the new value (PostgreSQL cannot drop an enum
value). Existing jobs are never rewritten or deleted.
"""

# ruff: noqa: E501  (constraint SQL is kept as single literal strings)

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9e4c2d7a1f3"
down_revision: str | Sequence[str] | None = "f1b6d2c8a9e4"
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
)

WORKSPACE_CHECK = "ck_background_jobs_workspace_required_except_email"
PREVIOUS_WORKSPACE_SQL = (
    "workspace_id IS NOT NULL OR job_type::text = 'transactional_email_delivery'"
)
NEW_WORKSPACE_SQL = (
    "workspace_id IS NOT NULL OR job_type::text IN "
    "('transactional_email_delivery', 'league_season_sync')"
)

EMAIL_PAYLOAD_CHECK = "ck_background_jobs_transactional_email_payload_shape"
EMAIL_PAYLOAD_SQL = "job_type::text <> 'transactional_email_delivery' OR COALESCE(jsonb_typeof(payload) = 'object' AND (payload - 'version' - 'delivery_id') = '{}'::jsonb AND payload ? 'version' AND payload ? 'delivery_id' AND jsonb_typeof(payload -> 'version') = 'number' AND jsonb_typeof(payload -> 'delivery_id') = 'number', false)"

SEASON_SYNC_CHECKS = (
    (
        "ck_background_jobs_league_season_sync_payload_shape",
        "job_type::text <> 'league_season_sync' OR COALESCE(payload_version = 1 AND jsonb_typeof(payload) = 'object' AND (payload - 'version' - 'season_id') = '{}'::jsonb AND jsonb_typeof(payload -> 'version') = 'number' AND payload ->> 'version' = '1' AND jsonb_typeof(payload -> 'season_id') = 'number' AND payload ->> 'season_id' ~ '^[1-9][0-9]{0,9}$', false)",
    ),
    (
        "ck_background_jobs_league_season_sync_key",
        "job_type::text <> 'league_season_sync' OR COALESCE(idempotency_key = 'league-season-sync:' || (payload ->> 'season_id'), false)",
    ),
)


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE background_job_type ADD VALUE 'league_season_sync'"))

    op.drop_constraint(WORKSPACE_CHECK, "background_jobs", type_="check")
    op.create_check_constraint(WORKSPACE_CHECK, "background_jobs", NEW_WORKSPACE_SQL)

    for name, sql in SEASON_SYNC_CHECKS:
        op.create_check_constraint(name, "background_jobs", sql)


def _refuse_if_any(bind: sa.Connection, description: str, query: str) -> None:
    count = bind.execute(sa.text(query)).scalar_one()

    if count:
        raise RuntimeError(
            f"Refusing to downgrade: {count} {description}. "
            "Remove the League-Season synchronization job state explicitly first."
        )


def downgrade() -> None:
    bind = op.get_bind()

    _refuse_if_any(
        bind,
        "league_season_sync background job(s) exist",
        "SELECT count(*) FROM background_jobs WHERE job_type::text = 'league_season_sync'",
    )
    _refuse_if_any(
        bind,
        "non-email background job(s) have a NULL workspace_id",
        "SELECT count(*) FROM background_jobs "
        "WHERE workspace_id IS NULL AND job_type::text <> 'transactional_email_delivery'",
    )
    _refuse_if_any(
        bind,
        "background job(s) would violate the prior workspace constraint",
        f"SELECT count(*) FROM background_jobs WHERE NOT ({PREVIOUS_WORKSPACE_SQL})",
    )

    for name, _sql in reversed(SEASON_SYNC_CHECKS):
        op.drop_constraint(name, "background_jobs", type_="check")

    op.drop_constraint(WORKSPACE_CHECK, "background_jobs", type_="check")
    op.drop_constraint(EMAIL_PAYLOAD_CHECK, "background_jobs", type_="check")

    op.execute(sa.text("ALTER TYPE background_job_type RENAME TO background_job_type_old"))
    previous_values = ", ".join(f"'{value}'" for value in PREVIOUS_JOB_TYPES)
    op.execute(sa.text(f"CREATE TYPE background_job_type AS ENUM ({previous_values})"))
    op.execute(
        sa.text(
            "ALTER TABLE background_jobs ALTER COLUMN job_type "
            "TYPE background_job_type "
            "USING job_type::text::background_job_type"
        )
    )
    op.execute(sa.text("DROP TYPE background_job_type_old"))

    op.create_check_constraint(EMAIL_PAYLOAD_CHECK, "background_jobs", EMAIL_PAYLOAD_SQL)
    op.create_check_constraint(WORKSPACE_CHECK, "background_jobs", PREVIOUS_WORKSPACE_SQL)
