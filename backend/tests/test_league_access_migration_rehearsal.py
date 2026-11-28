"""Rehearsal of migration f1b6d2c8a9e4 against a disposable rehearsal database.

Every test restores the database to head and removes its synthetic rows.
"""

import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests.support.migration_database import RehearsalDatabase

REVISION = "f1b6d2c8a9e4"
HEAD_REVISION = "d3a7e5b1c9f2"  # personal account identity; later than REVISION
PREVIOUS_REVISION = "a3c9e5f1b7d2"
ID = 990_501
NEW_TABLES = ("user_preferences", "user_league_season_access")
NEW_ENUMS = ("season_sync_state", "user_season_access_status", "user_season_proof_mode")


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


def cleanup_rows() -> None:
    execute(f"DELETE FROM user_league_season_access WHERE user_id = {ID}")
    execute(f"DELETE FROM user_preferences WHERE user_id = {ID}")
    execute(f"DELETE FROM leagues WHERE id = {ID}")
    execute(f"DELETE FROM users WHERE id = {ID}")
    execute(f"DELETE FROM provider_connections WHERE id = {ID}")
    execute(f"DELETE FROM installations WHERE id = {ID}")


def seed_user() -> None:
    execute(
        "INSERT INTO users (id, username, display_name, password_hash, role, is_active, "
        f"created_at, updated_at) VALUES ({ID}, 'rehearsal-{ID}', 'Rehearsal', "
        "'$argon2id$synthetic', 'spectator', true, now(), now())"
    )


def seed_season() -> None:
    execute(
        "INSERT INTO leagues (id, name, provider, provider_league_id, created_at, updated_at) "
        f"VALUES ({ID}, 'Rehearsal League', 'espn', '{ID}', now(), now())"
    )
    execute(
        "INSERT INTO seasons (id, league_id, year, current_week, regular_season_weeks, "
        f"created_at, updated_at) VALUES ({ID}, {ID}, 2025, 1, 14, now(), now())"
    )


def seed_workspace_connection(connection_id: int = ID) -> None:
    execute(
        "INSERT INTO installations (id, application_name, status, registration_status, "
        f"created_at, updated_at) VALUES ({ID}, 'Rehearsal', 'active', 'active', now(), now())"
    )
    execute(
        "INSERT INTO provider_connections (id, installation_id, provider, display_name, "
        "credential_schema_version, is_active, created_at, updated_at) "
        f"VALUES ({connection_id}, {ID}, 'espn', 'Legacy', 1, true, now(), now())"
    )


def seed_user_connection() -> None:
    seed_user()
    execute(
        "INSERT INTO provider_connections (id, user_id, provider, display_name, "
        "credential_schema_version, is_active, created_at, updated_at) "
        f"VALUES ({ID}, {ID}, 'espn', 'Personal', 1, true, now(), now())"
    )


def seed_preference() -> None:
    seed_user()
    execute(f"INSERT INTO user_preferences (user_id) VALUES ({ID})")


def seed_access() -> None:
    seed_user()
    seed_season()
    execute(
        "INSERT INTO user_league_season_access (user_id, season_id, proof_mode, verified_at) "
        f"VALUES ({ID}, {ID}, 'public', now())"
    )


def seed_orphan() -> None:
    seed_season()
    execute(f"UPDATE leagues SET orphaned_at = now() WHERE id = {ID}")


def seed_sync_state() -> None:
    seed_season()
    execute(f"UPDATE seasons SET sync_state = 'fresh' WHERE id = {ID}")


def seed_data_version() -> None:
    seed_season()
    execute(f"UPDATE seasons SET data_version = 3 WHERE id = {ID}")


def seed_failure_code() -> None:
    seed_season()
    execute(f"UPDATE seasons SET last_sync_failure_code = 'provider_unavailable' WHERE id = {ID}")


@pytest.fixture(autouse=True)
def restore_head() -> Iterator[None]:
    cleanup_rows()
    try:
        yield
    finally:
        alembic("upgrade", "head")
        cleanup_rows()


@pytest.mark.parametrize(
    ("seed", "fragment"),
    [
        (seed_preference, "user_preferences"),
        (seed_access, "user_league_season_access"),
        (seed_user_connection, "user-owned"),
        (seed_orphan, "orphaned_at"),
        (seed_sync_state, "synchronization state"),
        (seed_data_version, "synchronization state"),
        (seed_failure_code, "synchronization state"),
    ],
)
def test_downgrade_refuses_to_discard_target_state(seed, fragment) -> None:
    assert current_revisions() == [HEAD_REVISION]
    seed()

    result = alembic("downgrade", PREVIOUS_REVISION)

    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr and fragment in result.stderr
    assert current_revisions() == [HEAD_REVISION]
    assert set(NEW_TABLES) <= set(inspect(engine).get_table_names())


def test_round_trip_preserves_workspace_rows_and_restores_old_constraints() -> None:
    seed_workspace_connection()
    seed_season()

    assert alembic("downgrade", PREVIOUS_REVISION).returncode == 0
    assert current_revisions() == [PREVIOUS_REVISION]

    names = inspect(engine)
    assert not set(NEW_TABLES) & set(names.get_table_names())
    assert "user_id" not in {c["name"] for c in names.get_columns("provider_connections")}
    installation_column = next(
        c for c in names.get_columns("provider_connections") if c["name"] == "installation_id"
    )
    assert installation_column["nullable"] is False
    assert not {"orphaned_at"} & {c["name"] for c in names.get_columns("leagues")}
    assert not {"sync_state", "data_version"} & {c["name"] for c in names.get_columns("seasons")}
    assert not scalar(
        f"SELECT count(*) FROM pg_type WHERE typname IN ({', '.join(repr(e) for e in NEW_ENUMS)})"
    )
    assert scalar(f"SELECT count(*) FROM provider_connections WHERE id = {ID}") == 1
    assert scalar(f"SELECT count(*) FROM seasons WHERE id = {ID}") == 1

    assert alembic("upgrade", "head").returncode == 0
    assert current_revisions() == [HEAD_REVISION]
    assert alembic("check").returncode == 0
    assert scalar(f"SELECT data_version FROM seasons WHERE id = {ID}") == 0
    assert scalar(f"SELECT sync_state FROM seasons WHERE id = {ID}") is None


def test_upgrade_refuses_duplicate_canonical_league_identities() -> None:
    assert alembic("downgrade", PREVIOUS_REVISION).returncode == 0
    for league_id in (ID, ID + 1):
        execute(
            "INSERT INTO leagues (id, name, provider, provider_league_id, created_at, updated_at) "
            f"VALUES ({league_id}, 'Dup', 'espn', '{ID}', now(), now())"
        )
    try:
        result = alembic("upgrade", "head")
        assert result.returncode != 0
        assert "Refusing to upgrade" in result.stderr
        assert current_revisions() == [PREVIOUS_REVISION]
        assert scalar(f"SELECT count(*) FROM leagues WHERE id IN ({ID}, {ID + 1})") == 2
    finally:
        execute(f"DELETE FROM leagues WHERE id = {ID + 1}")
