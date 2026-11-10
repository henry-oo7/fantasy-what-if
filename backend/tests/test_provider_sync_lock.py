from typing import Any

import pytest

from app.services.sync_lock import (
    PROVIDER_SYNC_LOCK_NAMESPACE,
    SyncAlreadyRunningError,
    acquire_season_sync_lock,
)


class FakeSession:
    def __init__(
        self,
        acquired: bool | None,
    ) -> None:
        self.acquired = acquired
        self.statement: Any = None
        self.parameters: dict[str, int] | None = None
        self.calls = 0

    def scalar(
        self,
        statement: Any,
        parameters: dict[str, int],
    ) -> bool | None:
        self.calls += 1
        self.statement = statement
        self.parameters = parameters

        return self.acquired


def test_internal_season_lock_uses_stable_namespace() -> None:
    session = FakeSession(acquired=True)

    acquire_season_sync_lock(
        session,
        season_id=42,
    )

    assert session.calls == 1
    assert session.parameters == {
        "namespace": PROVIDER_SYNC_LOCK_NAMESPACE,
        "season_id": 42,
    }

    sql = str(session.statement)

    assert "pg_try_advisory_xact_lock" in sql
    assert ":namespace" in sql
    assert ":season_id" in sql


def test_internal_season_lock_rejects_concurrent_sync() -> None:
    session = FakeSession(acquired=False)

    with pytest.raises(
        SyncAlreadyRunningError,
        match="season 42",
    ):
        acquire_season_sync_lock(
            session,
            season_id=42,
        )


def test_null_lock_result_is_treated_as_failure() -> None:
    session = FakeSession(acquired=None)

    with pytest.raises(SyncAlreadyRunningError):
        acquire_season_sync_lock(
            session,
            season_id=42,
        )


@pytest.mark.parametrize(
    "season_id",
    [
        0,
        -1,
    ],
)
def test_internal_season_lock_requires_positive_id(
    season_id: int,
) -> None:
    session = FakeSession(acquired=True)

    with pytest.raises(
        ValueError,
        match="season_id must be positive",
    ):
        acquire_season_sync_lock(
            session,
            season_id=season_id,
        )

    assert session.calls == 0


def test_different_seasons_use_different_lock_keys() -> None:
    first = FakeSession(acquired=True)
    second = FakeSession(acquired=True)

    acquire_season_sync_lock(
        first,
        season_id=41,
    )
    acquire_season_sync_lock(
        second,
        season_id=42,
    )

    assert first.parameters != second.parameters
    assert first.parameters["namespace"] == second.parameters["namespace"]
