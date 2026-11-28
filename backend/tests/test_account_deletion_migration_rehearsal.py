"""Rehearsal of migration f4b8d2a6c9e1 in a disposable PostgreSQL database.

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

REVISION = "f4b8d2a6c9e1"
PREVIOUS_REVISION = "e8c3a6d1f4b7"
BASE = 992_100
LEAGUE = 992_101
KEY = f"orphan-league-purge:{LEAGUE}"
PAYLOAD = f'{{"version": 1, "league_id": {LEAGUE}}}'
PREFIX = "rehearsal-deletion"
NEW_CHECKS = {
    "ck_background_jobs_orphan_league_purge_payload_shape",
    "ck_background_jobs_orphan_league_purge_key",
}
RETAINED_CHECKS = {
    "ck_background_jobs_league_season_sync_payload_shape",
    "ck_background_jobs_league_season_sync_key",
    "ck_background_jobs_transactional_email_payload_shape",
    "ck_background_jobs_access_reverification_payload_shape",
    "ck_background_jobs_access_reverification_key",
}
WORKSPACE_CHECK = "ck_background_jobs_workspace_required_except_email"
SCOPE_CHECK = "ck_transactional_email_deliveries_scope_shape"

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


def enum_labels(name: str) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.scalars(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    f"WHERE t.typname = '{name}' ORDER BY e.enumsortorder"
                )
            )
        )


def check_definitions(table: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                f"WHERE conrelid = '{table}'::regclass AND contype = 'c'"
            )
        ).all()
    return {name: definition for name, definition in rows}


def insert_job(*, job_type: str, payload: str, key: str | None, payload_version: int = 1) -> None:
    key_value = f"'{key}'" if key is not None else "NULL"
    execute(
        "INSERT INTO background_jobs (workspace_id, job_type, idempotency_key, payload_version, "
        f"payload) VALUES (NULL, '{job_type}', {key_value}, {payload_version}, '{payload}'::jsonb)"
    )


def purge_job() -> None:
    insert_job(job_type="orphan_league_purge", payload=PAYLOAD, key=KEY)


def insert_delivery(
    key: str, *, scope: str, event: str, user: int | None, workspace: bool, encrypted: bool
) -> None:
    ciphertext_sql = "'ciphertext'" if encrypted else "NULL"
    key_version_sql = "1" if encrypted else "NULL"
    execute(
        "INSERT INTO transactional_email_deliveries (workspace_id, user_id, scope, event_type, "
        "status, available_at, attempt_count, maximum_attempts, idempotency_key, "
        "encrypted_recipient, recipient_key_version, created_at, updated_at) VALUES "
        f"({BASE if workspace else 'NULL'}, {'NULL' if user is None else BASE + user}, "
        f"'{scope}'::transactional_email_scope, '{event}'::transactional_email_event_type, "
        "'pending', now(), 0, 5, "
        f"'{PREFIX}-{key}', {ciphertext_sql}, {key_version_sql}, now(), now())"
    )


def completion(key: str = "completion") -> None:
    insert_delivery(
        key,
        scope="unscoped",
        event="account_deletion_completed",
        user=None,
        workspace=False,
        encrypted=True,
    )


def cleanup_rows() -> None:
    execute(f"DELETE FROM background_jobs WHERE idempotency_key = '{KEY}'")
    execute(f"DELETE FROM transactional_email_deliveries WHERE idempotency_key LIKE '{PREFIX}-%'")
    execute(f"DELETE FROM users WHERE id = {BASE + 1}")
    execute(f"DELETE FROM installations WHERE id = {BASE}")


@pytest.fixture(autouse=True)
def restore_head() -> Iterator[None]:
    alembic("upgrade", "head")
    cleanup_rows()
    execute(
        "INSERT INTO installations (id, application_name, status, registration_status, "
        f"created_at, updated_at) VALUES ({BASE}, 'Rehearsal', 'active', 'active', now(), now())"
    )
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

    assert enum_labels("background_job_type")[-1] == "orphan_league_purge"
    assert enum_labels("transactional_email_event_type")[-1] == "account_deletion_completed"
    jobs = {column["name"]: column for column in inspect(engine).get_columns("background_jobs")}
    assert jobs["workspace_id"]["nullable"] is True
    checks = check_definitions("background_jobs")
    assert set(checks) >= NEW_CHECKS | RETAINED_CHECKS
    assert "orphan_league_purge" in checks[WORKSPACE_CHECK]
    assert (
        "account_deletion_completed"
        in check_definitions("transactional_email_deliveries")[SCOPE_CHECK]
    )


def test_valid_unscoped_purge_job_is_accepted_and_active_key_is_unique() -> None:
    purge_job()

    assert scalar(f"SELECT count(*) FROM background_jobs WHERE idempotency_key = '{KEY}'") == 1
    with pytest.raises(IntegrityError):
        purge_job()


@pytest.mark.parametrize(
    ("payload", "key", "payload_version"),
    [
        ('{"version": 1}', KEY, 1),
        (f'{{"league_id": {LEAGUE}}}', KEY, 1),
        (f'{{"version": 1, "league_id": {LEAGUE}, "user_id": 1}}', KEY, 1),
        (f'{{"version": 1, "league_id": {LEAGUE}, "email": "x@example.invalid"}}', KEY, 1),
        (f'{{"version": 1, "league_id": "{LEAGUE}"}}', KEY, 1),
        ('{"version": 1, "league_id": 0}', "orphan-league-purge:0", 1),
        ('{"version": 1, "league_id": -1}', "orphan-league-purge:-1", 1),
        (f'{{"version": 2, "league_id": {LEAGUE}}}', KEY, 1),
        (PAYLOAD, KEY, 2),
        (PAYLOAD, None, 1),
        (PAYLOAD, "orphan-league-purge:1", 1),
        ("[]", KEY, 1),
    ],
)  # fmt: skip
def test_invalid_purge_jobs_are_rejected(
    payload: str, key: str | None, payload_version: int
) -> None:
    with pytest.raises(IntegrityError):
        insert_job(
            job_type="orphan_league_purge",
            payload=payload,
            key=key,
            payload_version=payload_version,
        )


def test_personal_completion_delivery_shape_is_enforced() -> None:
    completion()  # unscoped, no user, no workspace, encrypted recipient

    invalid = [
        dict(scope="unscoped", user=None, workspace=False, encrypted=False),  # no ciphertext
        dict(scope="unscoped", user=1, workspace=False, encrypted=True),  # user-owned completion
        dict(scope="unscoped", user=None, workspace=True, encrypted=True),  # workspace owner
        dict(scope="workspace", user=None, workspace=True, encrypted=False),  # scoped completion
    ]
    execute(
        "INSERT INTO users (id, display_name, email, normalized_email, password_hash, is_active, "
        f"created_at, updated_at) VALUES ({BASE + 1}, 'Rehearsal', '{PREFIX}@example.test', "
        f"'{PREFIX}@example.test', 'synthetic-hash', true, now(), now())"
    )
    for index, values in enumerate(invalid):
        with pytest.raises(IntegrityError):
            insert_delivery(f"bad-{index}", event="account_deletion_completed", **values)


def test_downgrade_refuses_while_a_purge_job_exists() -> None:
    purge_job()

    result = alembic("downgrade", PREVIOUS_REVISION)

    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr
    assert "orphan_league_purge background job(s) exist" in result.stderr
    assert current_revisions() == [REVISION]
    assert "orphan_league_purge" in enum_labels("background_job_type")
    assert scalar(f"SELECT count(*) FROM background_jobs WHERE idempotency_key = '{KEY}'") == 1


def test_downgrade_refuses_while_a_personal_completion_delivery_exists() -> None:
    completion()

    result = alembic("downgrade", PREVIOUS_REVISION)

    assert result.returncode != 0
    assert "account-deletion completion delivery(ies) exist" in result.stderr
    assert current_revisions() == [REVISION]
    assert (
        scalar(
            f"SELECT count(*) FROM transactional_email_deliveries WHERE idempotency_key = '{PREFIX}-completion'"
        )
        == 1
    )


def test_round_trip_restores_prior_schema_and_keeps_legacy_rows() -> None:
    insert_job(
        job_type="league_season_sync",
        payload='{"version": 1, "season_id": 992102}',
        key="league-season-sync:992102",
    )
    insert_delivery(
        "legacy-completion",
        scope="unscoped",
        event="workspace_deletion_completed",
        user=None,
        workspace=False,
        encrypted=True,
    )

    downgraded = alembic("downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    assert current_revisions() == [PREVIOUS_REVISION]
    assert "orphan_league_purge" not in enum_labels("background_job_type")
    assert "account_deletion_completed" not in enum_labels("transactional_email_event_type")
    checks = check_definitions("background_jobs")
    assert not NEW_CHECKS & set(checks) and set(checks) >= RETAINED_CHECKS
    assert "orphan_league_purge" not in checks[WORKSPACE_CHECK]
    assert (
        scalar(
            "SELECT count(*) FROM background_jobs WHERE idempotency_key = 'league-season-sync:992102'"
        )
        == 1
    )
    assert (
        scalar(
            f"SELECT count(*) FROM transactional_email_deliveries WHERE idempotency_key = '{PREFIX}-legacy-completion'"
        )
        == 1
    )

    upgraded = alembic("upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert current_revisions() == [REVISION]
    assert alembic("check").returncode == 0
    purge_job()
    completion("after-upgrade")
    execute("DELETE FROM background_jobs WHERE idempotency_key = 'league-season-sync:992102'")
