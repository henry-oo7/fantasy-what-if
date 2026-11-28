"""Add the personal access-reverification background job (ADR 0010).

Revision ID: e8c3a6d1f4b7
Revises: d3a7e5b1c9f2

Adds ``access_reverification`` to ``background_job_type``. A job of that type
may have a NULL ``workspace_id`` (it never carries one). Its payload must be
exactly ``{"version": 1, "user_id": <positive integer>, "season_id": <positive
integer>}`` and its idempotency key must be
``access-reverification:{user_id}:{season_id}``, so the existing partial unique
index allows at most one active reverification job per user and Season.

``ALTER TYPE ... ADD VALUE`` runs inside the migration transaction. The new
value is never cast before commit: every CHECK compares ``job_type::text``.

Downgrade refuses while any reverification job exists. When safe, it restores
the prior workspace CHECK and recreates ``background_job_type`` without the new
value (PostgreSQL cannot drop an enum value). Existing jobs are never rewritten
or deleted. Access rows and connections are untouched in both directions.
"""

# ruff: noqa: E501  (constraint SQL is kept as single literal strings)

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8c3a6d1f4b7"
down_revision: str | Sequence[str] | None = "d3a7e5b1c9f2"
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
)

WORKSPACE_CHECK = "ck_background_jobs_workspace_required_except_email"
PREVIOUS_WORKSPACE_SQL = (
    "workspace_id IS NOT NULL OR job_type::text IN "
    "('transactional_email_delivery', 'league_season_sync')"
)
NEW_WORKSPACE_SQL = (
    "workspace_id IS NOT NULL OR job_type::text IN "
    "('transactional_email_delivery', 'league_season_sync', 'access_reverification')"
)

# Checks that compare job_type are dropped and recreated around the enum
# retype on downgrade, exactly as the Season-sync migration does.
RETAINED_CHECKS = (
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
)

REVERIFICATION_CHECKS = (
    (
        "ck_background_jobs_access_reverification_payload_shape",
        "job_type::text <> 'access_reverification' OR COALESCE(payload_version = 1 AND jsonb_typeof(payload) = 'object' AND (payload - 'version' - 'user_id' - 'season_id') = '{}'::jsonb AND payload ->> 'version' = '1' AND jsonb_typeof(payload -> 'user_id') = 'number' AND payload ->> 'user_id' ~ '^[1-9][0-9]{0,9}$' AND jsonb_typeof(payload -> 'season_id') = 'number' AND payload ->> 'season_id' ~ '^[1-9][0-9]{0,9}$', false)",
    ),
    (
        "ck_background_jobs_access_reverification_key",
        "job_type::text <> 'access_reverification' OR COALESCE(idempotency_key = 'access-reverification:' || (payload ->> 'user_id') || ':' || (payload ->> 'season_id'), false)",
    ),
)


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE background_job_type ADD VALUE 'access_reverification'"))

    op.drop_constraint(WORKSPACE_CHECK, "background_jobs", type_="check")
    op.create_check_constraint(WORKSPACE_CHECK, "background_jobs", NEW_WORKSPACE_SQL)

    for name, sql in REVERIFICATION_CHECKS:
        op.create_check_constraint(name, "background_jobs", sql)


def downgrade() -> None:
    bind = op.get_bind()

    count = bind.execute(
        sa.text("SELECT count(*) FROM background_jobs WHERE job_type::text = 'access_reverification'")
    ).scalar_one()

    if count:
        raise RuntimeError(
            f"Refusing to downgrade: {count} access_reverification background job(s) exist. "
            "Remove the access-reverification job state explicitly first."
        )

    for name, _sql in reversed(REVERIFICATION_CHECKS):
        op.drop_constraint(name, "background_jobs", type_="check")

    for name, _sql in RETAINED_CHECKS:
        op.drop_constraint(name, "background_jobs", type_="check")

    op.drop_constraint(WORKSPACE_CHECK, "background_jobs", type_="check")

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

    for name, sql in RETAINED_CHECKS:
        op.create_check_constraint(name, "background_jobs", sql)

    op.create_check_constraint(WORKSPACE_CHECK, "background_jobs", PREVIOUS_WORKSPACE_SQL)
