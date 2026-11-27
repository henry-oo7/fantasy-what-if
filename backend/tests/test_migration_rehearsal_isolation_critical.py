"""Safety, lifecycle and column-slot regression tests for disposable rehearsal databases."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.core.settings import Settings
from app.db.session import engine
from tests.support.migration_database import (
    MAINTENANCE_DATABASE,
    PREFIX,
    assert_safe_target,
    column_slots,
    database_exists,
    drop_stale,
    rehearsal_database,
)

VALID = "fwi_rehearsal_0123456789ab_test"


@pytest.mark.parametrize(
    "name",
    [
        "fantasy_what_if",
        "fantasy_what_if_staging",
        "fantasy_what_if_test",
        "postgres",
        "fantasy_what_if_migration_test",  # another project database
        "fwi_rehearsal_0123456789ab",  # no _test suffix
        "fwi_rehearsal_0123456789ab_prod",
        "fwi_rehearsal_0123456789AB_test",  # not lowercase hex
        "fwi_rehearsal_0123456789a_test",  # wrong token length
        f"{VALID}x",
        f"x{VALID}",
        'fwi_rehearsal_0123456789ab_test"; DROP DATABASE fantasy_what_if; --',
        "fwi_rehearsal_0123456789ab_test\n",
        "",
    ],
)
def test_unsafe_names_are_refused(name: str) -> None:
    with pytest.raises(ValueError, match="Refusing"):
        assert_safe_target(name)


def test_database_is_fresh_dropped_after_success_and_not_the_shared_one() -> None:
    with rehearsal_database() as first:
        name = first.name
        assert name.startswith(PREFIX) and name.endswith("_test")
        assert first.scalar("SELECT current_database()") == name
        assert first.current_heads()
        assert database_exists(name)
    assert not database_exists(name)


def test_database_is_dropped_after_failure() -> None:
    with pytest.raises(AssertionError), rehearsal_database() as database:
        name = database.name
        raise AssertionError("simulated test failure")
    assert not database_exists(name)


def test_concurrent_rehearsal_databases_do_not_collide() -> None:
    with (
        rehearsal_database(upgrade_to_head=False) as one,
        rehearsal_database(upgrade_to_head=False) as two,
    ):
        assert one.name != two.name
        # The second database's stale sweep must not have dropped the live first one.
        assert database_exists(one.name) and database_exists(two.name)


def test_stale_sweep_drops_only_ownerless_rehearsal_databases() -> None:
    stale = "fwi_rehearsal_deadbeef0001_test"
    maintenance = create_engine(
        make_url(Settings().database_url).set(database=MAINTENANCE_DATABASE),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with maintenance.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {stale}"))
            connection.execute(text(f"CREATE DATABASE {stale}"))
            with rehearsal_database(upgrade_to_head=False) as live:
                # Creating `live` swept the ownerless `stale` database but not itself.
                assert not database_exists(stale)
                assert database_exists(live.name)
                assert live.name not in drop_stale(connection)
                assert database_exists(live.name)
            assert database_exists("fantasy_what_if_test")
    finally:
        with maintenance.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {stale}"))
        maintenance.dispose()


def test_rehearsals_do_not_consume_shared_test_database_column_slots() -> None:
    shared_before = column_slots(engine)

    with rehearsal_database() as first:
        fresh = first.column_slots()
        # Destructive round trip: drops and re-adds seasons columns in the rehearsal database.
        assert first.downgrade("a3c9e5f1b7d2").returncode == 0
        assert first.upgrade().returncode == 0
        used = first.column_slots()
        assert used[1] > fresh[1], "the round trip should leave dropped slots behind here"

        with rehearsal_database() as second:
            # A new rehearsal database starts fresh instead of inheriting those slots.
            assert second.column_slots() == fresh

    assert column_slots(engine) == shared_before
