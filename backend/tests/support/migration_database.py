"""Disposable PostgreSQL databases for destructive Alembic migration rehearsals.

Rehearsals downgrade and re-upgrade the schema. Doing that in the shared
``fantasy_what_if_test`` database leaks dropped-column attribute slots until
PostgreSQL's 1,600-slot limit is hit, so each rehearsal module gets its own
database that is dropped afterwards.

Safety model:
* Names are generated here, never supplied by tests, and must fullmatch
  ``fwi_rehearsal_<12 hex>_test`` (identifier-safe, 40 chars).
* The maintenance connection (``postgres`` database, credentials taken from the
  guarded test settings) only runs CREATE/DROP DATABASE, existence checks and
  advisory locks. Migrations only ever run against the rehearsal database.
* Each owner holds a session-level advisory lock (keyed on the name) from before
  CREATE until after DROP. A database whose lock can be taken has no live owner,
  so it is stale and safe to drop; SIGKILL releases the lock with the session.
* Alembic gets the URL through the child process ``env``, never ``os.environ``.
"""

import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.pool import NullPool

from app.core.settings import Settings

PREFIX = "fwi_rehearsal_"
NAME_PATTERN = re.compile(r"fwi_rehearsal_[0-9a-f]{12}_test")
PROTECTED_DATABASES = frozenset(
    {"fantasy_what_if", "fantasy_what_if_staging", "fantasy_what_if_test", "postgres"}
)
MAINTENANCE_DATABASE = "postgres"
BACKEND_DIR = Path(__file__).resolve().parents[2]


def assert_safe_target(name: str) -> str:
    """Return ``name`` only if it is a rehearsal database this module may mutate."""

    if name in PROTECTED_DATABASES or not NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Refusing to use {name!r} as a migration rehearsal database.")
    return name


def _base_url() -> URL:
    """Connection details from the guarded test settings; refuses non-test targets."""

    url = make_url(Settings().database_url)
    if url.database != "fantasy_what_if_test":
        raise RuntimeError("Migration rehearsals require the guarded fantasy_what_if_test URL.")
    return url


def _lock_key(connection: Connection, name: str) -> int:
    return connection.scalar(text("SELECT hashtextextended(:name, 0)"), {"name": name})


def _quote(connection: Connection, name: str) -> str:
    return connection.dialect.identifier_preparer.quote_identifier(assert_safe_target(name))


def _exists(connection: Connection, name: str) -> bool:
    return bool(
        connection.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name})
    )


def _drop(connection: Connection, name: str) -> None:
    # FORCE only terminates sessions attached to this one rehearsal database.
    connection.execute(text(f"DROP DATABASE IF EXISTS {_quote(connection, name)} WITH (FORCE)"))


def drop_stale(connection: Connection) -> list[str]:
    """Drop rehearsal databases whose owner is gone; skip any that are still locked."""

    dropped = []
    names = connection.scalars(
        text("SELECT datname FROM pg_database WHERE datname LIKE :pattern ORDER BY datname"),
        {"pattern": PREFIX.replace("_", r"\_") + "%"},
    ).all()
    for name in names:
        if not NAME_PATTERN.fullmatch(name) or name in PROTECTED_DATABASES:
            continue
        key = _lock_key(connection, name)
        if connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            try:
                _drop(connection, name)
                dropped.append(name)
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    return dropped


class RehearsalDatabase:
    """A live disposable database plus the helpers migration tests need."""

    def __init__(self, name: str, url: URL) -> None:
        self.name = assert_safe_target(name)
        self.url = url
        self.engine: Engine = create_engine(url, poolclass=NullPool)

    def alembic(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert_safe_target(self.name)
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env={**os.environ, "DATABASE_URL": self.url.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def upgrade(self, revision: str = "head") -> subprocess.CompletedProcess[str]:
        return self.alembic("upgrade", revision)

    def downgrade(self, revision: str) -> subprocess.CompletedProcess[str]:
        return self.alembic("downgrade", revision)

    def current_heads(self) -> list[str]:
        output = self.alembic("current").stdout
        return [line.split()[0] for line in output.splitlines() if line and line[0].isalnum()]

    def scalar(self, sql: str):
        with self.engine.connect() as connection:
            return connection.scalar(text(sql))

    def column_slots(self, table: str = "seasons") -> tuple[int, int, int]:
        return column_slots(self.engine, table)


def column_slots(engine: Engine, table: str = "seasons") -> tuple[int, int, int]:
    """(live, dropped, total) attribute slots of ``public.<table>``."""

    with engine.connect() as connection:
        live, dropped, total = connection.execute(
            text(
                "SELECT count(*) FILTER (WHERE NOT attisdropped), "
                "count(*) FILTER (WHERE attisdropped), count(*) "
                "FROM pg_attribute WHERE attrelid = to_regclass(:table) AND attnum > 0"
            ),
            {"table": f"public.{table}"},
        ).one()
    return live, dropped, total


def database_exists(name: str) -> bool:
    maintenance = create_engine(
        _base_url().set(database=MAINTENANCE_DATABASE),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with maintenance.connect() as connection:
            return _exists(connection, name)
    finally:
        maintenance.dispose()


@contextmanager
def rehearsal_database(*, upgrade_to_head: bool = True) -> Iterator[RehearsalDatabase]:
    """Create a fresh rehearsal database, yield it, and always drop it afterwards."""

    base = _base_url()
    name = assert_safe_target(f"{PREFIX}{uuid.uuid4().hex[:12]}_test")
    maintenance = create_engine(
        base.set(database=MAINTENANCE_DATABASE),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    with maintenance.connect() as owner:
        # Held on this connection until it closes, so the lock outlives the database.
        owner.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _lock_key(owner, name)})
        database: RehearsalDatabase | None = None
        try:
            drop_stale(owner)
            owner.execute(text(f"CREATE DATABASE {_quote(owner, name)}"))
            database = RehearsalDatabase(name, base.set(database=name))
            with database.engine.connect() as connection:
                if connection.scalar(text("SELECT current_database()")) != name:
                    raise RuntimeError("Rehearsal connection reached an unexpected database.")
            if upgrade_to_head:
                result = database.upgrade()
                if result.returncode != 0:
                    raise RuntimeError(f"Rehearsal upgrade failed:\n{result.stderr}")
            yield database
        finally:
            if database is not None:
                database.engine.dispose()
            _drop(owner, name)
            if _exists(owner, name):
                raise RuntimeError(f"Rehearsal database {name} was not dropped.")
    maintenance.dispose()
