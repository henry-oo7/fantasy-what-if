"""Routine personal Season maintenance behavior: due dates, job contract, batching.

Real PostgreSQL (``fantasy_what_if_test``); synthetic rows only; no provider calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.platform_enums import (
    BackgroundJobType,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.session import SessionFactory
from app.repositories.background_job_repository import BackgroundJobRepository
from app.scripts.sync_scheduler import enqueue_maintenance_batch
from app.services.access_reverification import REVERIFICATION_PRIORITY
from app.services.background_job_handlers import AccessReverificationJobHandler
from app.services.background_job_worker import BackgroundJobExecutionError, BackgroundJobSnapshot
from app.services.season_maintenance import (
    JOB_STAGGER,
    SEASON_REFRESH_INTERVAL,
    list_due_reverifications,
    list_due_season_syncs,
)
from app.services.synchronization_job_contract import (
    InvalidAccessReverificationPayloadError,
    access_reverification_idempotency_key,
    build_access_reverification_payload,
    validate_access_reverification_payload,
)
from tests.support.personal_maintenance import (
    SEASON_YEAR,
    World,
    jobs_for,
    scope_discovery,
    update_access,
    update_season,
)

NOW = datetime.now(UTC)
ACTIVE = UserSeasonAccessStatus.ACTIVE
FLAGGED = UserSeasonAccessStatus.REVERIFICATION_REQUIRED
PUBLIC = UserSeasonProofMode.PUBLIC


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    created = World()
    scope_discovery(monkeypatch, created)
    yield created
    created.cleanup()


def due_pairs(world: World) -> set[tuple[int, int]]:
    with SessionFactory() as session:
        return {
            pair
            for pair in list_due_reverifications(session, now=datetime.now(UTC), limit=10_000)
            if pair[0] in world.users
        }


def due_seasons(world: World) -> set[int]:
    with SessionFactory() as session:
        own = {
            season_id
            for (season_id,) in session.execute(
                text("SELECT id FROM seasons WHERE league_id = ANY(:ids)"), {"ids": world.leagues}
            )
        }
        return own & set(list_due_season_syncs(session, now=datetime.now(UTC), limit=10_000))


# --------------------------------------------------------------------------
# Reverification due dates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verified_ago", "due"),
    [(timedelta(days=6, hours=23), False), (timedelta(days=7, minutes=1), True)],
)
def test_active_access_is_due_seven_days_after_verification(
    world: World, verified_ago: timedelta, due: bool
) -> None:
    target = world.season()
    member = world.member(target, verified_ago=verified_ago)

    assert ((member.user_id, target.season_id) in due_pairs(world)) is due


def test_last_reverified_at_replaces_verified_at(world: World) -> None:
    target = world.season()
    member = world.member(target, verified_ago=timedelta(days=30))
    update_access(member, target.season_id, last_reverified_at=NOW - timedelta(days=1))

    assert due_pairs(world) == set()

    update_access(member, target.season_id, last_reverified_at=NOW - timedelta(days=8))

    assert due_pairs(world) == {(member.user_id, target.season_id)}


def test_flagged_access_is_retried_weekly_from_when_it_was_flagged(world: World) -> None:
    target = world.season()
    fresh_flag = world.member(target, status=FLAGGED, verified_ago=timedelta(days=90))
    old_flag = world.member(target, status=FLAGGED, verified_ago=timedelta(days=90))
    update_access(fresh_flag, target.season_id, updated_at=NOW - timedelta(days=1))
    update_access(old_flag, target.season_id, updated_at=NOW - timedelta(days=8))

    assert due_pairs(world) == {(old_flag.user_id, target.season_id)}


def test_revoked_inactive_and_orphaned_are_never_due(world: World) -> None:
    target = world.season()
    world.member(target, status=UserSeasonAccessStatus.REVOKED)
    world.member(target, user_active=False)
    orphaned = world.season(orphaned_at=NOW)
    world.member(orphaned, status=FLAGGED, verified_ago=timedelta(days=90))

    assert due_pairs(world) == set()


def test_reverifications_are_oldest_first_and_bounded(world: World) -> None:
    target = world.season()
    newer = world.member(target, verified_ago=timedelta(days=8))
    older = world.member(target, verified_ago=timedelta(days=20))

    with SessionFactory() as session:
        pairs = [
            pair
            for pair in list_due_reverifications(session, now=datetime.now(UTC), limit=10_000)
            if pair[0] in (older.user_id, newer.user_id)
        ]
        assert pairs == [(older.user_id, target.season_id), (newer.user_id, target.season_id)]
        assert len(list_due_reverifications(session, now=NOW, limit=1)) == 1


# --------------------------------------------------------------------------
# Season refresh due policy
# --------------------------------------------------------------------------


def _public_season(world: World, **values):
    target = world.season(**values)
    world.member(target, proof=PUBLIC, verified_ago=timedelta(days=1))
    return target


@pytest.mark.parametrize(
    ("success_ago", "attempt_ago", "due"),
    [
        (None, None, True),  # never synchronized
        (timedelta(days=6), None, False),
        (SEASON_REFRESH_INTERVAL + timedelta(minutes=1), None, True),
        (timedelta(days=10), timedelta(hours=2), False),  # failing Season: retried at most daily
        (timedelta(days=10), timedelta(hours=25), True),
    ],
)
def test_season_refresh_due_policy(
    world: World, success_ago: timedelta | None, attempt_ago: timedelta | None, due: bool
) -> None:
    target = _public_season(world)
    update_season(
        target.season_id,
        last_successful_sync_at=None if success_ago is None else NOW - success_ago,
        last_sync_attempt_at=None if attempt_ago is None else NOW - attempt_ago,
    )

    assert (target.season_id in due_seasons(world)) is due


@pytest.mark.parametrize("state", [SeasonSyncState.QUEUED, SeasonSyncState.SYNCING])
def test_active_attempts_are_not_scheduled_again(world: World, state: SeasonSyncState) -> None:
    assert _public_season(world, sync_state=state).season_id not in due_seasons(world)


def test_legacy_null_state_is_scheduled_not_read_as_fresh(world: World) -> None:
    assert _public_season(world, sync_state=None).season_id in due_seasons(world)


def test_historical_seasons_are_not_polled(world: World) -> None:
    assert _public_season(world, year=SEASON_YEAR - 3).season_id not in due_seasons(world)


def test_season_needs_a_usable_credential_source(world: World) -> None:
    rejected_only = world.season()
    world.member(rejected_only, connection_error="credentials_rejected", verified_ago=timedelta(1))
    flagged_only = world.season()
    world.member(flagged_only, proof=PUBLIC, status=FLAGGED, verified_ago=timedelta(1))
    usable = world.season()
    world.member(usable, verified_ago=timedelta(days=1))

    assert due_seasons(world) == {usable.season_id}


def test_batch_staggers_jobs_and_counts_without_identifiers(world: World) -> None:
    target = _public_season(world)
    due = world.member(target, verified_ago=timedelta(days=9))

    batch = enqueue_maintenance_batch(now=NOW)

    assert batch is not None and batch.sync_created >= 1 and batch.reverification_created >= 1
    [sync] = jobs_for(f"league-season-sync:{target.season_id}")
    [reverification] = jobs_for(f"access-reverification:{due.user_id}:{target.season_id}")
    assert sync.available_at >= NOW and reverification.available_at >= NOW
    assert (sync.available_at - NOW) % JOB_STAGGER == timedelta(0)
    assert reverification.priority == REVERIFICATION_PRIORITY


# --------------------------------------------------------------------------
# Job contract
# --------------------------------------------------------------------------


def test_payload_and_key_are_exact() -> None:
    payload = build_access_reverification_payload(7, 9)

    assert payload == {"version": 1, "user_id": 7, "season_id": 9}
    assert access_reverification_idempotency_key(7, 9) == "access-reverification:7:9"
    assert validate_access_reverification_payload(payload, payload_version=1) == (7, 9)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "user_id": 7},
        {"version": 1, "user_id": 7, "season_id": 9, "email": "x@example.invalid"},
        {"version": 1, "user_id": True, "season_id": 9},
        {"version": 1, "user_id": 0, "season_id": 9},
        {"version": 2, "user_id": 7, "season_id": 9},
        {"version": 1, "user_id": "7", "season_id": 9},
    ],
)
def test_invalid_payloads_are_rejected(payload: dict) -> None:
    with pytest.raises(InvalidAccessReverificationPayloadError):
        validate_access_reverification_payload(payload, payload_version=1)


def test_handler_rejects_an_invalid_payload() -> None:
    handler = AccessReverificationJobHandler(
        session_factory=SessionFactory, settings=SimpleNamespace()
    )
    job = BackgroundJobSnapshot(
        id=1,
        workspace_id=None,
        job_type=BackgroundJobType.ACCESS_REVERIFICATION,
        payload_version=1,
        payload={"version": 1, "user_id": 1},
        attempt_count=1,
        maximum_attempts=3,
    )

    with pytest.raises(BackgroundJobExecutionError) as caught:
        handler(job)

    assert caught.value.safe_error_code == "invalid_job_payload"


def test_repository_requires_the_user_and_season_key() -> None:
    with SessionFactory() as session, pytest.raises(ValueError, match="key"):
        BackgroundJobRepository(session).enqueue(
            workspace_id=None,
            job_type=BackgroundJobType.ACCESS_REVERIFICATION,
            payload=build_access_reverification_payload(7, 9),
            idempotency_key="access-reverification:7:10",
        )


@pytest.mark.parametrize(
    ("payload", "key"),
    [
        ('{"version": 1, "user_id": 7, "season_id": 9, "x": 1}', "access-reverification:7:9"),
        ('{"version": 1, "user_id": 7}', "access-reverification:7:9"),
        ('{"version": 1, "user_id": 7, "season_id": 9}', "access-reverification:7:10"),
        ('{"version": 1, "user_id": 7, "season_id": 9}', None),
        ('{"version": 1, "user_id": "7", "season_id": 9}', "access-reverification:7:9"),
    ],
)
def test_database_rejects_malformed_reverification_jobs(payload: str, key: str | None) -> None:
    with SessionFactory() as session, pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO background_jobs (job_type, status, priority, payload_version, "
                "payload, attempt_count, maximum_attempts, available_at, idempotency_key, "
                "created_at, updated_at) VALUES ('access_reverification', 'pending', 0, 1, "
                "CAST(:payload AS jsonb), 0, 3, now(), :key, now(), now())"
            ),
            {"payload": payload, "key": key},
        )
        session.commit()
