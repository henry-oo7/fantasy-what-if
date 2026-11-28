"""Rehearsal of migration e8c3a6d1f4b7 in a disposable PostgreSQL database.

Every test restores the database to head and removes its synthetic rows.
"""

# ruff: noqa: E501  (parameter table rows are kept on one line each)

import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from tests.support.migration_database import RehearsalDatabase

REVISION = "e8c3a6d1f4b7"
PREVIOUS_REVISION = "d3a7e5b1c9f2"
KEY = "access-reverification:991001:991002"
PAYLOAD = '{"version": 1, "user_id": 991001, "season_id": 991002}'
SEASON_KEY = "league-season-sync:991002"
NEW_CHECKS = {
    "ck_background_jobs_access_reverification_payload_shape",
    "ck_background_jobs_access_reverification_key",
}
RETAINED_CHECKS = {
    "ck_background_jobs_league_season_sync_payload_shape",
    "ck_background_jobs_league_season_sync_key",
    "ck_background_jobs_transactional_email_payload_shape",
}
WORKSPACE_CHECK = "ck_background_jobs_workspace_required_except_email"

DB: RehearsalDatabase
engine: Engine  # both bound to this module's disposable database by _bind_database


@pytest.fixture(scope="module", autouse=True)
def _bind_database(migration_database: RehearsalDatabase) -> None:
    global DB, engine
    DB, engine = migration_database, migration_database.engine


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return DB.alembic(*args)


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


def insert_job(*, job_type: str, payload: str, key: str | None, payload_version: int = 1) -> None:
    key_value = f"'{key}'" if key is not None else "NULL"
    execute(
        "INSERT INTO background_jobs (workspace_id, job_type, idempotency_key, payload_version, "
        f"payload) VALUES (NULL, '{job_type}', {key_value}, {payload_version}, '{payload}'::jsonb)"
    )


def reverification_job() -> None:
    insert_job(job_type="access_reverification", payload=PAYLOAD, key=KEY)


def season_job() -> None:
    insert_job(
        job_type="league_season_sync",
        payload='{"version": 1, "season_id": 991002}',
        key=SEASON_KEY,
    )


def cleanup_rows() -> None:
    execute(f"DELETE FROM background_jobs WHERE idempotency_key IN ('{KEY}', '{SEASON_KEY}')")


@pytest.fixture(autouse=True)
def restore_head() -> Iterator[None]:
    cleanup_rows()
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

    assert job_types()[-2:] == ["league_season_sync", "access_reverification"]
    jobs = {column["name"]: column for column in inspect(engine).get_columns("background_jobs")}
    assert jobs["workspace_id"]["nullable"] is True
    checks = check_definitions()
    assert set(checks) >= NEW_CHECKS | RETAINED_CHECKS
    assert "access_reverification" in checks[WORKSPACE_CHECK]


def test_valid_unscoped_job_is_accepted_and_active_key_is_unique() -> None:
    reverification_job()

    assert scalar(f"SELECT count(*) FROM background_jobs WHERE idempotency_key = '{KEY}'") == 1
    with pytest.raises(IntegrityError):
        reverification_job()


@pytest.mark.parametrize(
    ("payload", "key", "payload_version"),
    [
        ('{"version": 1, "user_id": 991001}', KEY, 1),
        ('{"version": 1, "season_id": 991002}', KEY, 1),
        ('{"version": 1, "user_id": 991001, "season_id": 991002, "email": "x@example.invalid"}', KEY, 1),
        ('{"version": 1, "user_id": 991001, "season_id": 991002, "provider_connection_id": 1}', KEY, 1),
        ('{"version": 1, "user_id": "991001", "season_id": 991002}', KEY, 1),
        ('{"version": 1, "user_id": 0, "season_id": 991002}', "access-reverification:0:991002", 1),
        ('{"version": 1, "user_id": 991001, "season_id": -1}', "access-reverification:991001:-1", 1),
        ('{"version": 2, "user_id": 991001, "season_id": 991002}', KEY, 1),
        ('{"version": 1, "user_id": 991001, "season_id": 991002}', KEY, 2),
        ('{"version": 1, "user_id": 991001, "season_id": 991002}', None, 1),
        ('{"version": 1, "user_id": 991001, "season_id": 991002}', "access-reverification:991001:1", 1),
        ('{"version": 1, "user_id": 991001, "season_id": 991002}', "access-reverification:1:991002", 1),
        ("[]", KEY, 1),
    ],
)  # fmt: skip
def test_invalid_jobs_are_rejected(payload: str, key: str | None, payload_version: int) -> None:
    with pytest.raises(IntegrityError):
        insert_job(
            job_type="access_reverification",
            payload=payload,
            key=key,
            payload_version=payload_version,
        )


def test_downgrade_refuses_while_a_reverification_job_exists() -> None:
    season_job()
    reverification_job()

    result = alembic("downgrade", PREVIOUS_REVISION)

    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr
    assert "access_reverification background job(s) exist" in result.stderr
    assert current_revisions() == [REVISION]
    assert "access_reverification" in job_types()
    assert scalar(f"SELECT count(*) FROM background_jobs WHERE idempotency_key = '{KEY}'") == 1


def test_round_trip_restores_prior_constraints_and_keeps_existing_jobs() -> None:
    season_job()
    reverification_job()
    execute(f"DELETE FROM background_jobs WHERE idempotency_key = '{KEY}'")

    downgraded = alembic("downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    assert current_revisions() == [PREVIOUS_REVISION]
    assert "access_reverification" not in job_types()
    assert job_types()[-1] == "league_season_sync"
    checks = check_definitions()
    assert not NEW_CHECKS & set(checks)
    assert set(checks) >= RETAINED_CHECKS
    assert "access_reverification" not in checks[WORKSPACE_CHECK]
    assert (
        scalar(f"SELECT count(*) FROM background_jobs WHERE idempotency_key = '{SEASON_KEY}'") == 1
    )

    upgraded = alembic("upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert current_revisions() == [REVISION]
    assert alembic("check").returncode == 0
    reverification_job()
