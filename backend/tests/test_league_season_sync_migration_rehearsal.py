"""Rehearsal of migration b9e4c2d7a1f3 against the guarded test database.

Every test restores the database to head and removes its synthetic rows.
"""

# ruff: noqa: E501  (parameter table rows are kept on one line each)

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine

REVISION = "b9e4c2d7a1f3"
PREVIOUS_REVISION = "f1b6d2c8a9e4"
BACKEND_DIR = Path(__file__).resolve().parents[1]
ID = 990_601
SEASON_KEY = f"league-season-sync:{ID}"
NEW_CHECKS = {
    "ck_background_jobs_league_season_sync_payload_shape",
    "ck_background_jobs_league_season_sync_key",
}
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


def execute(sql: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql))


def scalar(sql: str):
    with engine.connect() as connection:
        return connection.scalar(text(sql))


def current_revisions() -> list[str]:
    output = alembic("current").stdout
    return [line.split()[0] for line in output.splitlines() if line and line[0].isalnum()]


def job_types() -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.scalars(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'background_job_type' ORDER BY e.enumsortorder"
                )
            )
        )


def check_definitions() -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'background_jobs'::regclass AND contype = 'c'"
            )
        ).all()
    return {name: definition for name, definition in rows}


def cleanup_rows() -> None:
    execute(
        "DELETE FROM background_jobs WHERE workspace_id = "
        f"{ID} OR idempotency_key LIKE 'rehearsal-{ID}-%' OR idempotency_key = '{SEASON_KEY}'"
    )
    execute(f"DELETE FROM installations WHERE id = {ID}")


def seed_workspace() -> None:
    execute(
        "INSERT INTO installations (id, application_name, status, registration_status, "
        f"created_at, updated_at) VALUES ({ID}, 'Rehearsal', 'active', 'active', now(), now())"
    )


def insert_job(
    *,
    job_type: str,
    workspace: bool,
    payload: str,
    key: str | None,
    payload_version: int = 1,
) -> None:
    workspace_value = str(ID) if workspace else "NULL"
    key_value = f"'{key}'" if key is not None else "NULL"
    execute(
        "INSERT INTO background_jobs (workspace_id, job_type, idempotency_key, payload_version, "
        f"payload) VALUES ({workspace_value}, '{job_type}', {key_value}, {payload_version}, "
        f"'{payload}'::jsonb)"
    )


def season_job(*, workspace: bool = False) -> None:
    insert_job(
        job_type="league_season_sync",
        workspace=workspace,
        payload=f'{{"version": 1, "season_id": {ID}}}',
        key=SEASON_KEY,
    )


def legacy_and_email_jobs() -> None:
    insert_job(
        job_type="incremental_season_sync",
        workspace=True,
        payload='{"season_id": 1, "full_sync": false, "trigger": "rehearsal"}',
        key=f"rehearsal-{ID}-legacy",
    )
    insert_job(
        job_type="transactional_email_delivery",
        workspace=False,
        payload='{"version": 1, "delivery_id": 1}',
        key=f"rehearsal-{ID}-email",
    )


@pytest.fixture(autouse=True)
def restore_head() -> Iterator[None]:
    cleanup_rows()
    seed_workspace()
    try:
        yield
    finally:
        alembic("upgrade", "head")
        cleanup_rows()


def test_head_schema_shape() -> None:
    assert current_revisions() == [REVISION]
    heads = alembic("heads").stdout.strip().splitlines()
    assert len(heads) == 1 and heads[0].startswith(REVISION)
    assert alembic("check").returncode == 0

    assert job_types()[-1] == "league_season_sync"
    jobs = {column["name"]: column for column in inspect(engine).get_columns("background_jobs")}
    assert jobs["workspace_id"]["nullable"] is True
    checks = check_definitions()
    assert set(checks) >= NEW_CHECKS
    assert "league_season_sync" in checks[WORKSPACE_CHECK]


def test_valid_jobs_are_accepted() -> None:
    legacy_and_email_jobs()
    season_job(workspace=False)
    execute(f"DELETE FROM background_jobs WHERE idempotency_key = '{SEASON_KEY}'")
    season_job(workspace=True)

    assert scalar(f"SELECT count(*) FROM background_jobs WHERE workspace_id = {ID}") == 2


@pytest.mark.parametrize(
    ("job_type", "workspace", "payload", "key", "payload_version"),
    [
        ("incremental_season_sync", False, '{"season_id": 1}', None, 1),
        ("workspace_deletion", False, "{}", None, 1),
        ("league_season_sync", False, '{"version": 1}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"season_id": 990601}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"version": 1, "season_id": 990601, "user_id": 1}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"version": 1, "season_id": 990601, "workspace_id": 1}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"version": 1, "season_id": 990601, "provider_connection_id": 1}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"version": 1, "season_id": 990601, "full_sync": true}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"version": 1, "season_id": "990601"}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"version": 1, "season_id": 0}', "league-season-sync:0", 1),
        ("league_season_sync", False, '{"version": 1, "season_id": -5}', "league-season-sync:-5", 1),
        ("league_season_sync", False, '{"version": 1, "season_id": 1.5}', "league-season-sync:1.5", 1),
        ("league_season_sync", False, '{"version": 2, "season_id": 990601}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"version": "1", "season_id": 990601}', SEASON_KEY, 1),
        ("league_season_sync", False, '{"version": 1, "season_id": 990601}', SEASON_KEY, 2),
        ("league_season_sync", False, '{"version": 1, "season_id": 990601}', None, 1),
        ("league_season_sync", False, '{"version": 1, "season_id": 990601}', "league-season-sync:1", 1),
        ("league_season_sync", False, "[]", SEASON_KEY, 1),
    ],
)  # fmt: skip
def test_invalid_jobs_are_rejected(
    job_type: str, workspace: bool, payload: str, key: str | None, payload_version: int
) -> None:
    with pytest.raises(IntegrityError):
        insert_job(
            job_type=job_type,
            workspace=workspace,
            payload=payload,
            key=key,
            payload_version=payload_version,
        )


def test_downgrade_refuses_while_a_season_sync_job_exists() -> None:
    legacy_and_email_jobs()
    season_job()

    result = alembic("downgrade", PREVIOUS_REVISION)

    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr
    assert "league_season_sync background job(s) exist" in result.stderr
    assert current_revisions() == [REVISION]
    assert "league_season_sync" in job_types()
    assert scalar(f"SELECT count(*) FROM background_jobs WHERE idempotency_key = '{SEASON_KEY}'")


def test_round_trip_restores_prior_constraints_and_keeps_existing_jobs() -> None:
    legacy_and_email_jobs()
    season_job()
    before = scalar(
        "SELECT string_agg(id::text || ':' || job_type::text || ':' || "
        "coalesce(workspace_id::text, '-'), ',' ORDER BY id) FROM background_jobs "
        f"WHERE idempotency_key LIKE 'rehearsal-{ID}-%'"
    )

    assert alembic("downgrade", PREVIOUS_REVISION).returncode != 0
    execute(f"DELETE FROM background_jobs WHERE idempotency_key = '{SEASON_KEY}'")

    downgraded = alembic("downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    assert current_revisions() == [PREVIOUS_REVISION]
    assert "league_season_sync" not in job_types()
    assert job_types()[-1] == "transactional_email_delivery"
    checks = check_definitions()
    assert not NEW_CHECKS & set(checks)
    assert "league_season_sync" not in checks[WORKSPACE_CHECK]
    assert "ck_background_jobs_transactional_email_payload_shape" in checks
    assert (
        scalar(
            "SELECT string_agg(id::text || ':' || job_type::text || ':' || "
            "coalesce(workspace_id::text, '-'), ',' ORDER BY id) FROM background_jobs "
            f"WHERE idempotency_key LIKE 'rehearsal-{ID}-%'"
        )
        == before
    )

    # The prior workspace rule is back in force.
    with pytest.raises(IntegrityError):
        insert_job(
            job_type="incremental_season_sync",
            workspace=False,
            payload="{}",
            key=f"rehearsal-{ID}-null",
        )

    upgraded = alembic("upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert current_revisions() == [REVISION]
    assert alembic("check").returncode == 0
    heads = alembic("heads").stdout.strip().splitlines()
    assert len(heads) == 1 and heads[0].startswith(REVISION)
    season_job()
