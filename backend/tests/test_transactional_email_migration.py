"""Downgrade guards and round trip for migration d7f2b9a4c6e1 (ADR 0009 schema).

Runs Alembic in a subprocess against the guarded ``_test`` database and always
restores it to head, even on failure.
"""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.db.session import engine

REVISION = "d7f2b9a4c6e1"
PREVIOUS_REVISION = "c4e8a1f7d3b9"
HEAD_REVISION = "a3c9e5f1b7d2"  # account-token delivery link; later than REVISION
BACKEND_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_ID = 990_201
PREVIOUS_JOB_TYPES = [
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
]
EMAIL_PAYLOAD = '\'{"version": 1, "delivery_id": 1}\'::jsonb'
WORKSPACE_CHECK = "ck_background_jobs_workspace_required_except_email"


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    engine.dispose()
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def current_revisions() -> list[str]:
    output = alembic("current").stdout
    return [line.split()[0] for line in output.splitlines() if line and line[0].isalnum()]


def execute(sql: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql))


def scalar(sql: str):
    with engine.connect() as connection:
        return connection.scalar(text(sql))


def cleanup() -> None:
    execute(
        "DELETE FROM transactional_email_deliveries WHERE idempotency_key LIKE 'migration-test:%'"
    )
    execute(
        "DELETE FROM background_jobs WHERE idempotency_key LIKE 'migration-test:%' "
        f"OR workspace_id = {WORKSPACE_ID}"
    )
    execute(f"DELETE FROM installations WHERE id = {WORKSPACE_ID}")


def seed_workspace() -> None:
    execute(
        "INSERT INTO installations (id, application_name, status, registration_status, "
        "created_at, updated_at) "
        f"VALUES ({WORKSPACE_ID}, 'Migration Test', 'active', 'active', now(), now())"
    )


def seed_delivery() -> None:
    seed_workspace()
    execute(
        "INSERT INTO transactional_email_deliveries (workspace_id, scope, event_type, "
        "idempotency_key) VALUES "
        f"({WORKSPACE_ID}, 'workspace', 'workspace_deletion_requested', 'migration-test:d')"
    )


def seed_scoped_email_job() -> None:
    seed_workspace()
    execute(
        "INSERT INTO background_jobs (workspace_id, job_type, payload, idempotency_key) VALUES "
        f"({WORKSPACE_ID}, 'transactional_email_delivery', {EMAIL_PAYLOAD}, 'migration-test:j')"
    )


def seed_null_workspace_email_job() -> None:
    execute(
        "INSERT INTO background_jobs (workspace_id, job_type, payload, idempotency_key) VALUES "
        f"(NULL, 'transactional_email_delivery', {EMAIL_PAYLOAD}, 'migration-test:n')"
    )


@pytest.fixture
def at_head() -> Iterator[None]:
    assert scalar("SELECT current_database()").endswith("_test"), (
        "migration tests may only touch a *_test database"
    )

    upgrade = alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    # This module rehearses revision d7f2b9a4c6e1 itself, so step back from
    # any later head. Later revisions have their own migration tests.
    stepped_back = alembic("downgrade", REVISION)
    assert stepped_back.returncode == 0, stepped_back.stderr
    assert current_revisions() == [REVISION]

    cleanup()

    try:
        yield
    finally:
        engine.dispose()
        cleanup()
        restored = alembic("upgrade", "head")
        assert restored.returncode == 0, restored.stderr


def test_upgraded_schema_shape(at_head: None) -> None:
    inspector = inspect(engine)
    jobs = {column["name"]: column for column in inspector.get_columns("background_jobs")}

    assert "transactional_email_deliveries" in inspector.get_table_names()
    assert jobs["workspace_id"]["nullable"] is True

    with engine.connect() as connection:
        constraints = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'background_jobs'::regclass"
                )
            ).scalars()
        )
        labels = (
            connection.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'background_job_type' ORDER BY e.enumsortorder"
                )
            )
            .scalars()
            .all()
        )

    assert {
        WORKSPACE_CHECK,
        "ck_background_jobs_transactional_email_payload_shape",
    } <= constraints
    assert labels == [*PREVIOUS_JOB_TYPES, "transactional_email_delivery"]


@pytest.mark.parametrize(
    ("seed", "message"),
    [
        (seed_delivery, "transactional email delivery row(s) exist"),
        (seed_scoped_email_job, "transactional_email_delivery background job(s) exist"),
        (seed_null_workspace_email_job, "transactional_email_delivery background job(s) exist"),
    ],
)
def test_downgrade_refuses_email_state(
    at_head: None, seed: Callable[[], None], message: str
) -> None:
    seed()

    result = alembic("downgrade", "-1")

    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr
    assert message in result.stderr
    assert current_revisions() == [REVISION]
    assert "transactional_email_deliveries" in inspect(engine).get_table_names()


def test_downgrade_refuses_any_null_workspace_job(at_head: None) -> None:
    """The NULL-workspace guard, isolated from the email-job guard.

    The CHECK normally makes a non-email NULL-workspace job impossible, so the
    test lifts the CHECK briefly and restores it exactly in ``finally``.
    """

    constraint_sql = scalar(
        f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = '{WORKSPACE_CHECK}'"
    )
    assert constraint_sql

    execute(f"ALTER TABLE background_jobs DROP CONSTRAINT {WORKSPACE_CHECK}")
    try:
        execute(
            "INSERT INTO background_jobs (workspace_id, job_type, payload, idempotency_key) "
            "VALUES (NULL, 'full_season_sync', '{}'::jsonb, 'migration-test:null')"
        )

        result = alembic("downgrade", "-1")

        assert result.returncode != 0
        assert "Refusing to downgrade" in result.stderr
        assert "NULL workspace_id" in result.stderr
        assert current_revisions() == [REVISION]
    finally:
        execute("DELETE FROM background_jobs WHERE idempotency_key = 'migration-test:null'")
        execute(f"ALTER TABLE background_jobs ADD CONSTRAINT {WORKSPACE_CHECK} {constraint_sql}")


def test_safe_downgrade_preserves_jobs_and_round_trips(at_head: None) -> None:
    seed_workspace()
    execute(
        "INSERT INTO background_jobs (workspace_id, job_type, payload, idempotency_key) VALUES "
        f"({WORKSPACE_ID}, 'full_season_sync', '{{}}'::jsonb, 'migration-test:keep')"
    )

    downgrade = alembic("downgrade", "-1")
    assert downgrade.returncode == 0, downgrade.stderr
    assert current_revisions() == [PREVIOUS_REVISION]

    inspector = inspect(engine)
    jobs = {column["name"]: column for column in inspector.get_columns("background_jobs")}
    assert "transactional_email_deliveries" not in inspector.get_table_names()
    assert jobs["workspace_id"]["nullable"] is False

    with engine.connect() as connection:
        labels = (
            connection.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'background_job_type' ORDER BY e.enumsortorder"
                )
            )
            .scalars()
            .all()
        )
        leftovers = (
            connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname LIKE 'transactional_email_%' "
                    "OR typname = 'background_job_type_old'"
                )
            )
            .scalars()
            .all()
        )
        preserved = connection.scalar(
            text(
                "SELECT count(*) FROM background_jobs WHERE idempotency_key = 'migration-test:keep'"
            )
        )

    assert labels == PREVIOUS_JOB_TYPES
    assert leftovers == []
    assert preserved == 1

    upgrade = alembic("upgrade", REVISION)
    assert upgrade.returncode == 0, upgrade.stderr
    assert current_revisions() == [REVISION]

    to_head = alembic("upgrade", "head")
    assert to_head.returncode == 0, to_head.stderr
    assert current_revisions() == [HEAD_REVISION]

    check = alembic("check")
    assert check.returncode == 0, check.stderr
    assert "No new upgrade operations detected" in check.stdout + check.stderr

    heads = alembic("heads")
    head_lines = [line for line in heads.stdout.splitlines() if "(head)" in line]
    assert len(head_lines) == 1
    assert head_lines[0].startswith(HEAD_REVISION)
