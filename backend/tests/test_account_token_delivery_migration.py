"""Rehearsal of migration a3c9e5f1b7d2 (account-token delivery link, ADR 0009).

Runs Alembic in a subprocess against the guarded ``_test`` database and always
restores it to head, even on failure.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine

REVISION = "a3c9e5f1b7d2"
PREVIOUS_REVISION = "d7f2b9a4c6e1"
HEAD_REVISION = "b9e4c2d7a1f3"  # League-Season sync job; later than REVISION
BACKEND_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_ID = 990_301
USER_ID = 990_301
TABLES = ("email_verification_tokens", "password_reset_tokens")


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
    for table in TABLES:
        execute(f"DELETE FROM {table} WHERE user_id = {USER_ID}")
    execute(
        "DELETE FROM transactional_email_deliveries "
        "WHERE idempotency_key LIKE 'token-migration-test:%'"
    )
    execute(f"DELETE FROM users WHERE id = {USER_ID}")
    execute(f"DELETE FROM installations WHERE id = {WORKSPACE_ID}")


def seed_user() -> None:
    execute(
        "INSERT INTO installations (id, application_name, status, registration_status, "
        "created_at, updated_at) "
        f"VALUES ({WORKSPACE_ID}, 'Token Migration Test', 'active', 'active', now(), now())"
    )
    execute(
        "INSERT INTO users (id, username, display_name, workspace_id, password_hash, role, "
        "is_active, created_at, updated_at) VALUES "
        f"({USER_ID}, 'token-migration-test', 'Token Migration', {WORKSPACE_ID}, "
        "'$argon2id$synthetic', 'admin', true, now(), now())"
    )


def seed_delivery(key: str = "token-migration-test:d") -> int:
    with engine.begin() as connection:
        return connection.scalar(
            text(
                "INSERT INTO transactional_email_deliveries (workspace_id, user_id, scope, "
                "event_type, idempotency_key) VALUES "
                f"({WORKSPACE_ID}, {USER_ID}, 'workspace', "
                "'administrator_email_verification', :key) RETURNING id"
            ),
            {"key": key},
        )


def insert_token(table: str, suffix: str, delivery_id: str, attempt: str) -> None:
    execute(
        f"INSERT INTO {table} (user_id, token_hash, expires_at, "
        "transactional_email_delivery_id, delivery_attempt) VALUES "
        f"({USER_ID}, '{suffix.ljust(64, '0')}', now() + interval '1 hour', "
        f"{delivery_id}, {attempt})"
    )


@pytest.fixture
def at_head() -> Iterator[None]:
    assert scalar("SELECT current_database()").endswith("_test"), (
        "migration tests may only touch a *_test database"
    )

    upgrade = alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
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


def test_upgraded_columns_constraints_and_indexes(at_head: None) -> None:
    inspector = inspect(engine)

    for table in TABLES:
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert columns["transactional_email_delivery_id"]["nullable"] is True
        assert columns["delivery_attempt"]["nullable"] is True

        checks = {check["name"] for check in inspector.get_check_constraints(table)}
        assert {
            f"ck_{table}_delivery_attempt_pair",
            f"ck_{table}_delivery_attempt_positive",
        } <= checks

        indexes = {index["name"]: index for index in inspector.get_indexes(table)}
        assert indexes[f"ix_{table}_user_delivery"]["column_names"] == [
            "user_id",
            "transactional_email_delivery_id",
        ]

        delete_rule = scalar(
            "SELECT confdeltype FROM pg_constraint "
            f"WHERE conname = 'fk_{table}_transactional_email_delivery_id'"
        )
        assert delete_rule == "c"


@pytest.mark.parametrize("table", TABLES)
def test_pairing_and_positive_attempt_are_enforced(at_head: None, table: str) -> None:
    seed_user()
    delivery_id = seed_delivery()

    insert_token(table, "linked", str(delivery_id), "1")
    insert_token(table, "legacy", "NULL", "NULL")

    for suffix, linked_id, attempt in (
        ("partial-a", str(delivery_id), "NULL"),
        ("partial-b", "NULL", "1"),
        ("zero", str(delivery_id), "0"),
        ("negative", str(delivery_id), "-1"),
    ):
        with pytest.raises(IntegrityError):
            insert_token(table, suffix, linked_id, attempt)

    execute(f"DELETE FROM transactional_email_deliveries WHERE id = {delivery_id}")

    remaining = scalar(f"SELECT count(*) FROM {table} WHERE user_id = {USER_ID}")
    linked = scalar(
        f"SELECT count(*) FROM {table} WHERE transactional_email_delivery_id IS NOT NULL"
    )
    assert remaining == 1
    assert linked == 0


@pytest.mark.parametrize("table", TABLES)
def test_downgrade_refuses_while_linked_tokens_exist(at_head: None, table: str) -> None:
    seed_user()
    delivery_id = seed_delivery()
    insert_token(table, "linked", str(delivery_id), "2")

    result = alembic("downgrade", "-1")

    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr
    assert table in result.stderr
    assert current_revisions() == [REVISION]


def test_safe_downgrade_preserves_legacy_tokens_and_round_trips(at_head: None) -> None:
    seed_user()
    for table in TABLES:
        insert_token(table, f"legacy-{table[:5]}", "NULL", "NULL")

    downgrade = alembic("downgrade", "-1")
    assert downgrade.returncode == 0, downgrade.stderr
    assert current_revisions() == [PREVIOUS_REVISION]

    inspector = inspect(engine)

    for table in TABLES:
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "transactional_email_delivery_id" not in columns
        assert "delivery_attempt" not in columns
        assert scalar(f"SELECT count(*) FROM {table} WHERE user_id = {USER_ID}") == 1

    upgrade = alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    assert current_revisions() == [HEAD_REVISION]

    for table in TABLES:
        assert (
            scalar(
                f"SELECT count(*) FROM {table} WHERE user_id = {USER_ID} "
                "AND transactional_email_delivery_id IS NULL AND delivery_attempt IS NULL"
            )
            == 1
        )

    check = alembic("check")
    assert check.returncode == 0, check.stderr
    assert "No new upgrade operations detected" in check.stdout + check.stderr

    heads = alembic("heads")
    head_lines = [line for line in heads.stdout.splitlines() if "(head)" in line]
    assert len(head_lines) == 1
    assert head_lines[0].startswith(HEAD_REVISION)
