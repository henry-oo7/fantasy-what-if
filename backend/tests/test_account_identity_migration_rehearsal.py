"""Rehearsal of migration d3a7e5b1c9f2 (personal account identity).

Runs only against a disposable rehearsal database. Every test restores the
database to head and removes its synthetic rows. All data is synthetic.
"""

# ruff: noqa: E501  (SQL fixtures are kept one statement per line)

import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from tests.support.migration_database import RehearsalDatabase

REVISION = "d3a7e5b1c9f2"
PREVIOUS_REVISION = "b9e4c2d7a1f3"
BASE = 991_700
WORKSPACE = BASE
PREFIX = "rehearsal-identity"
SCOPE_CHECK = "ck_transactional_email_deliveries_scope_shape"
IDENTITY_CHECK = "ck_users_identity_shape"

DB: RehearsalDatabase
engine: Engine


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


def cleanup() -> None:
    execute(f"DELETE FROM transactional_email_deliveries WHERE idempotency_key LIKE '{PREFIX}-%'")
    execute(f"DELETE FROM users WHERE id BETWEEN {BASE} AND {BASE + 99}")
    execute(f"DELETE FROM installations WHERE id = {WORKSPACE}")


def seed_workspace() -> None:
    execute(
        "INSERT INTO installations (id, application_name, status, registration_status, "
        f"created_at, updated_at) VALUES ({WORKSPACE}, 'Rehearsal', 'active', 'active', now(), now())"
    )


def insert_user(
    offset: int,
    *,
    role: str | None,
    username: str | None,
    workspace: bool,
    email: str | None,
    normalized: str | None = "same",
    display_name: str = "Rehearsal",
) -> None:
    def literal(value: str | None) -> str:
        return "NULL" if value is None else f"'{value}'"

    normalized_value = email if normalized == "same" else normalized
    role_sql = "NULL" if role is None else f"'{role}'::user_role"
    execute(
        "INSERT INTO users (id, username, display_name, workspace_id, email, normalized_email, "
        "password_hash, role, is_active, created_at, updated_at) VALUES "
        f"({BASE + offset}, {literal(username)}, '{display_name}', {WORKSPACE if workspace else 'NULL'}, "
        f"{literal(email)}, {literal(normalized_value)}, 'synthetic-hash', "
        f"{role_sql}, true, now(), now())"
    )


def personal(offset: int, email: str | None = None, **overrides: object) -> None:
    insert_user(
        offset,
        role=None,
        username=None,
        workspace=False,
        email=email or f"{PREFIX}-{offset}@example.test",
        **overrides,  # type: ignore[arg-type]
    )


def legacy_admin(offset: int, email: str | None = None) -> None:
    insert_user(
        offset,
        role="admin",
        username=f"{PREFIX}-admin-{offset}",
        workspace=True,
        email=email or f"{PREFIX}-admin-{offset}@example.test",
    )


def legacy_spectator(offset: int, *, email: str | None = None, workspace: bool = True) -> None:
    insert_user(
        offset,
        role="spectator",
        username=f"{PREFIX}-spec-{offset}",
        workspace=workspace,
        email=email,
    )


def insert_delivery(
    key: str,
    *,
    scope: str,
    event: str,
    user: int | None,
    workspace: bool = False,
    encrypted: bool = False,
) -> None:
    ciphertext_sql = "'ciphertext'" if encrypted else "NULL"
    key_version_sql = "1" if encrypted else "NULL"
    execute(
        "INSERT INTO transactional_email_deliveries (workspace_id, user_id, scope, event_type, "
        "status, available_at, attempt_count, maximum_attempts, idempotency_key, "
        "encrypted_recipient, recipient_key_version, created_at, updated_at) VALUES "
        f"({WORKSPACE if workspace else 'NULL'}, {'NULL' if user is None else BASE + user}, "
        f"'{scope}'::transactional_email_scope, '{event}'::transactional_email_event_type, "
        "'pending', now(), 0, 5, "
        f"'{PREFIX}-{key}', {ciphertext_sql}, {key_version_sql}, now(), now())"
    )


@pytest.fixture(autouse=True)
def restore_head() -> Iterator[None]:
    alembic("upgrade", "head")
    cleanup()
    seed_workspace()
    try:
        yield
    finally:
        alembic("upgrade", "head")
        cleanup()


def test_head_schema_shape() -> None:
    assert current_revisions() == [REVISION]
    heads = alembic("heads").stdout.strip().splitlines()
    assert len(heads) == 1 and heads[0].startswith(REVISION)
    assert alembic("check").returncode == 0

    columns = {column["name"]: column for column in inspect(engine).get_columns("users")}
    assert columns["role"]["nullable"] is True and columns["role"]["default"] is None
    assert columns["username"]["nullable"] is True
    assert columns["workspace_id"]["nullable"] is True

    indexes = {index["name"]: index for index in inspect(engine).get_indexes("users")}
    assert "uq_users_normalized_email" in indexes
    assert "uq_users_admin_normalized_email" not in indexes
    assert indexes["uq_users_normalized_email"]["unique"]


def test_valid_personal_and_legacy_shapes_are_accepted() -> None:
    personal(1)
    legacy_admin(2)
    legacy_spectator(3)

    assert scalar(f"SELECT count(*) FROM users WHERE id BETWEEN {BASE} AND {BASE + 99}") == 3


@pytest.mark.parametrize(
    "case",
    [
        "personal_without_email",
        "personal_with_role",
        "personal_with_username",
        "personal_with_workspace",
        "personal_blank_display_name",
        "personal_email_not_normalized_email",
        "legacy_without_username",
        "legacy_without_role",
    ],
)
def test_invalid_user_shapes_are_rejected(case: str) -> None:
    attempts = {
        "personal_without_email": lambda: insert_user(
            1, role=None, username=None, workspace=False, email=None
        ),
        "personal_with_role": lambda: insert_user(
            1, role="admin", username=None, workspace=False, email="a@example.test"
        ),
        "personal_with_username": lambda: insert_user(
            1, role=None, username="named", workspace=False, email="a@example.test"
        ),
        "personal_with_workspace": lambda: insert_user(
            1, role=None, username=None, workspace=True, email="a@example.test"
        ),
        "personal_blank_display_name": lambda: personal(1, display_name="   "),
        "personal_email_not_normalized_email": lambda: personal(
            1, email="Mixed@example.test", normalized="mixed@example.test"
        ),
        "legacy_without_username": lambda: insert_user(
            1, role="admin", username=None, workspace=True, email="a@example.test"
        ),
        "legacy_without_role": lambda: insert_user(
            1, role=None, username="legacy", workspace=True, email="a@example.test"
        ),
    }

    with pytest.raises(IntegrityError):
        attempts[case]()


def test_normalized_email_is_globally_unique_across_every_shape() -> None:
    shared = f"{PREFIX}-shared@example.test"
    legacy_admin(1, email=shared)

    with pytest.raises(IntegrityError):
        personal(2, email=shared)

    # A spectator carrying the same email is rejected too, so email login can
    # never become ambiguous.
    with pytest.raises(IntegrityError):
        insert_user(3, role="spectator", username="spec-dup", workspace=True, email=shared)


def test_user_identity_deliveries_satisfy_the_exact_ownership_rules() -> None:
    personal(1)

    for event in (
        "administrator_email_verification",
        "administrator_password_reset",
        "administrator_password_changed",
    ):
        insert_delivery(event, scope="unscoped", event=event, user=1)

    legacy_admin(2)
    insert_delivery(
        "workspace", scope="workspace", event="administrator_password_reset", user=2, workspace=True
    )

    assert (
        scalar(
            f"SELECT count(*) FROM transactional_email_deliveries WHERE idempotency_key LIKE '{PREFIX}-%'"
        )
        == 4
    )


@pytest.mark.parametrize(
    "case",
    [
        "user_scoped_completion_event",
        "user_scoped_deletion_event",
        "user_scoped_with_ciphertext",
        "user_scoped_with_workspace",
        "unscoped_identity_without_user",
        "workspace_scope_without_workspace",
    ],
)
def test_invalid_delivery_ownership_is_rejected(case: str) -> None:
    personal(1)
    attempts = {
        "user_scoped_completion_event": lambda: insert_delivery(
            "bad", scope="unscoped", event="workspace_deletion_completed", user=1
        ),
        "user_scoped_deletion_event": lambda: insert_delivery(
            "bad", scope="unscoped", event="workspace_deletion_requested", user=1
        ),
        "user_scoped_with_ciphertext": lambda: insert_delivery(
            "bad", scope="unscoped", event="administrator_password_reset", user=1, encrypted=True
        ),
        "user_scoped_with_workspace": lambda: insert_delivery(
            "bad", scope="unscoped", event="administrator_password_reset", user=1, workspace=True
        ),
        "unscoped_identity_without_user": lambda: insert_delivery(
            "bad", scope="unscoped", event="administrator_password_reset", user=None
        ),
        "workspace_scope_without_workspace": lambda: insert_delivery(
            "bad", scope="workspace", event="administrator_password_reset", user=1
        ),
    }

    with pytest.raises(IntegrityError):
        attempts[case]()


def test_upgrade_refuses_duplicate_normalized_emails_without_naming_them() -> None:
    assert alembic("downgrade", PREVIOUS_REVISION).returncode == 0
    secret_email = f"{PREFIX}-private@example.test"
    # Previously representable: an administrator and a spectator sharing an email.
    legacy_admin(1, email=secret_email)
    insert_user(
        2,
        role="spectator",
        username="dup-spec",
        workspace=True,
        email=secret_email.upper(),
        normalized=secret_email,
    )

    result = alembic("upgrade", "head")

    assert result.returncode != 0
    assert "Refusing to upgrade" in result.stderr
    assert secret_email.casefold() not in (result.stderr + result.stdout).casefold()
    assert current_revisions() == [PREVIOUS_REVISION]
    # Nothing was merged or removed.
    assert scalar(f"SELECT count(*) FROM users WHERE id IN ({BASE + 1}, {BASE + 2})") == 2
    execute(f"DELETE FROM users WHERE id = {BASE + 2}")
    assert alembic("upgrade", "head").returncode == 0


def test_downgrade_refuses_while_a_personal_user_exists_and_keeps_it() -> None:
    personal(1)

    result = alembic("downgrade", PREVIOUS_REVISION)

    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr
    assert current_revisions() == [REVISION]
    assert scalar(f"SELECT count(*) FROM users WHERE id = {BASE + 1}") == 1


def test_downgrade_refuses_a_user_owned_delivery_even_without_a_personal_user() -> None:
    legacy_admin(1)
    insert_delivery("user-owned", scope="unscoped", event="administrator_password_reset", user=1)

    result = alembic("downgrade", PREVIOUS_REVISION)

    assert result.returncode != 0
    assert "user-owned identity delivery" in result.stderr
    assert current_revisions() == [REVISION]


def test_safe_downgrade_preserves_legacy_rows_and_reupgrades() -> None:
    legacy_admin(1)
    legacy_spectator(2)
    insert_delivery(
        "legacy",
        scope="workspace",
        event="administrator_email_verification",
        user=1,
        workspace=True,
    )
    before = scalar(
        "SELECT string_agg(id::text || ':' || coalesce(role::text, '-') || ':' || coalesce(username, '-') "
        f"|| ':' || coalesce(workspace_id::text, '-'), ',' ORDER BY id) FROM users WHERE id BETWEEN {BASE} AND {BASE + 99}"
    )

    downgraded = alembic("downgrade", PREVIOUS_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    assert current_revisions() == [PREVIOUS_REVISION]

    columns = {column["name"]: column for column in inspect(engine).get_columns("users")}
    assert columns["role"]["nullable"] is False
    assert columns["username"]["nullable"] is False
    assert "spectator" in str(columns["role"]["default"])
    indexes = {index["name"] for index in inspect(engine).get_indexes("users")}
    assert "uq_users_admin_normalized_email" in indexes
    assert "uq_users_normalized_email" not in indexes
    assert (
        scalar(
            "SELECT string_agg(id::text || ':' || role::text || ':' || username || ':' || coalesce(workspace_id::text, '-'), ',' "
            f"ORDER BY id) FROM users WHERE id BETWEEN {BASE} AND {BASE + 99}"
        )
        == before
    )
    assert (
        scalar(
            f"SELECT count(*) FROM transactional_email_deliveries WHERE idempotency_key = '{PREFIX}-legacy'"
        )
        == 1
    )

    # The previous schema cannot represent a personal user again.
    with pytest.raises(IntegrityError):
        execute(
            "INSERT INTO users (id, display_name, email, normalized_email, password_hash, is_active, created_at, updated_at) "
            f"VALUES ({BASE + 9}, 'x', 'x@example.test', 'x@example.test', 'h', true, now(), now())"
        )

    upgraded = alembic("upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert current_revisions() == [REVISION]
    assert alembic("check").returncode == 0
