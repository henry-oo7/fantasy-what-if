"""Downgrade guards and round trip for migration c4e8a1f7d3b9 (FR-20 schema).

These tests really downgrade and re-upgrade the guarded ``_test`` database,
through Alembic in a subprocess so Alembic's logging setup cannot leak into
other tests. Every test restores the database to head, even on failure.

The deletion-lifecycle CHECK makes a ``deletion_pending`` workspace and a
workspace with deletion timestamps the same valid database state: a pending
row must carry both timestamps and a non-pending row must carry neither. The
"pending status" and "deletion timestamps" downgrade guards therefore overlap
for every valid row, and are covered together by one case. The constraint is
deliberately not bypassed to isolate them.
"""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.db.session import engine

REVISION = "c4e8a1f7d3b9"
PREVIOUS_REVISION = "b7d41e9c2a10"
HEAD_REVISION = "d7f2b9a4c6e1"  # transactional email schema; later than REVISION
BACKEND_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_ID = 990_101
USER_ID = 990_101
ORIGINAL_REVOCATION_REASONS = [
    "replaced",
    "logout",
    "password_reset",
    "spectator_password_reset",
    "spectator_disabled",
]


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


def cleanup_seed_rows() -> None:
    execute(f"DELETE FROM auth_sessions WHERE user_id = {USER_ID}")
    execute(f"DELETE FROM workspace_deletion_records WHERE former_workspace_id = {WORKSPACE_ID}")
    execute(f"DELETE FROM users WHERE id = {USER_ID}")
    execute(f"DELETE FROM installations WHERE id = {WORKSPACE_ID}")


@pytest.fixture
def at_head() -> Iterator[None]:
    with engine.connect() as connection:
        database = connection.scalar(text("SELECT current_database()"))

    assert isinstance(database, str)
    assert database.endswith("_test"), "migration tests may only touch a *_test database"

    upgrade = alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    # This module rehearses revision c4e8a1f7d3b9 itself, so step back from
    # any later head. Later revisions have their own migration tests.
    stepped_back = alembic("downgrade", REVISION)
    assert stepped_back.returncode == 0, stepped_back.stderr
    assert current_revisions() == [REVISION]

    cleanup_seed_rows()

    try:
        yield
    finally:
        engine.dispose()
        cleanup_seed_rows()
        restored = alembic("upgrade", "head")
        assert restored.returncode == 0, restored.stderr


def seed_workspace(*, pending: bool) -> None:
    status = (
        "'deletion_pending', now(), now() + interval '7 days'"
        if pending
        else "'active', NULL, NULL"
    )
    execute(
        "INSERT INTO installations (id, application_name, status, registration_status, "
        "deletion_requested_at, deletion_scheduled_for) "
        f"VALUES ({WORKSPACE_ID}, 'migration-test', 'active', {status})"
    )


def seed_session(*, purpose: str, revocation_reason: str | None) -> None:
    seed_workspace(pending=False)
    execute(
        "INSERT INTO users (id, username, display_name, workspace_id, password_hash, role, "
        f"is_active) VALUES ({USER_ID}, 'migration-test-user', 'm', {WORKSPACE_ID}, 'x', "
        "'spectator', true)"
    )
    revoked = (
        f", revoked_at, revocation_reason) VALUES ({USER_ID}, repeat('a', 64), 'c', "
        f"now() + interval '1 day', '{purpose}', now(), '{revocation_reason}')"
        if revocation_reason
        else f") VALUES ({USER_ID}, repeat('a', 64), 'c', now() + interval '1 day', '{purpose}')"
    )
    execute(
        "INSERT INTO auth_sessions (user_id, session_token_hash, csrf_token_hash, expires_at, "
        "purpose" + revoked
    )


def seed_deletion_record() -> None:
    execute(
        "INSERT INTO workspace_deletion_records (former_workspace_id, provider, "
        "normalized_provider_league_id, requested_at, scheduled_for, completed_at, outcome, "
        f"retention_expires_at) VALUES ({WORKSPACE_ID}, 'espn', '1', now() - interval '8 days', "
        "now() - interval '1 day', now(), 'completed', now() + interval '90 days')"
    )


GUARD_CASES: list[tuple[str, Callable[[], None], str]] = [
    # Valid pending workspaces always carry both timestamps, so the
    # deletion_pending guard and the timestamp guard overlap here.
    (
        "pending_status_and_timestamps_overlap",
        lambda: seed_workspace(pending=True),
        "workspace(s) carry deletion timestamps",
    ),
    (
        "deletion_recovery_session",
        lambda: seed_session(purpose="deletion_recovery", revocation_reason=None),
        "non-standard purpose",
    ),
    (
        "workspace_deletion_requested_revocation",
        lambda: seed_session(purpose="standard", revocation_reason="workspace_deletion_requested"),
        "workspace_deletion_requested revocation reason",
    ),
    (
        "deletion_record",
        seed_deletion_record,
        "workspace deletion record(s) exist",
    ),
]


@pytest.mark.parametrize(
    ("seed", "message"),
    [pytest.param(seed, message, id=name) for name, seed, message in GUARD_CASES],
)
def test_downgrade_is_refused_while_deletion_state_exists(
    at_head: None,
    seed: Callable[[], None],
    message: str,
) -> None:
    seed()

    result = alembic("downgrade", "-1")

    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr
    assert message in result.stderr
    assert current_revisions() == [REVISION]
    assert "workspace_deletion_records" in inspect(engine).get_table_names()


def test_downgrade_round_trip_after_guarded_data_is_removed(at_head: None) -> None:
    seed_session(purpose="standard", revocation_reason=None)
    cleanup_seed_rows()

    downgrade = alembic("downgrade", "-1")
    assert downgrade.returncode == 0, downgrade.stderr
    assert current_revisions() == [PREVIOUS_REVISION]

    inspector = inspect(engine)
    assert "workspace_deletion_records" not in inspector.get_table_names()
    assert "purpose" not in {column["name"] for column in inspector.get_columns("auth_sessions")}
    assert not {"deletion_requested_at", "deletion_scheduled_for"} & {
        column["name"] for column in inspector.get_columns("installations")
    }

    with engine.connect() as connection:
        reasons = (
            connection.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'session_revocation_reason' ORDER BY e.enumsortorder"
                )
            )
            .scalars()
            .all()
        )
        leftover_types = (
            connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('auth_session_purpose', 'session_revocation_reason_old', "
                    "'workspace_deletion_outcome')"
                )
            )
            .scalars()
            .all()
        )

    assert reasons == ORIGINAL_REVOCATION_REASONS
    assert leftover_types == []

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
    assert heads.returncode == 0, heads.stderr
    head_lines = [line for line in heads.stdout.splitlines() if "(head)" in line]
    assert len(head_lines) == 1
    assert head_lines[0].startswith(HEAD_REVISION)
