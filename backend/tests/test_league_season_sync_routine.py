"""Routine coverage for League-Season synchronization jobs (ADR 0010).

Real PostgreSQL in ``fantasy_what_if_test``. Database tests run in a rolled
back outer transaction. Atomicity, concurrency and stale-worker guarantees
are covered by the critical suites, not here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_current_user, require_session_csrf
from app.db import model_registry  # noqa: F401
from app.db.auth_models import User, UserRole
from app.db.models import League, Season, SyncRun, SyncStatus
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    SeasonSyncState,
)
from app.db.platform_models import BackgroundJob
from app.db.session import SessionFactory
from app.main import app
from app.repositories.background_job_repository import BackgroundJobRepository
from app.services.admin_synchronization_jobs import (
    ADMIN_SYNC_PRIORITY,
    enqueue_administrator_synchronization,
)
from app.services.scheduled_synchronization_jobs import (
    SCHEDULED_SYNC_PRIORITY,
    ScheduledSynchronizationTarget,
    enqueue_scheduled_synchronization,
)
from app.services.season_sync_jobs import (
    EXPIRED_LEASE_SYNC_CODE,
    LEASE_EXPIRED_SYNC_MESSAGE,
    SEASON_SYNC_MAXIMUM_ATTEMPTS,
    SeasonSyncSeasonNotFoundError,
    apply_season_sync_lease_recovery,
    enqueue_season_sync,
)
from app.services.season_sync_state import (
    GENERIC_SYNC_FAILURE_CODE,
    has_successful_data,
    mark_failed,
    mark_queued,
    mark_succeeded,
    mark_syncing,
    safe_failure_code,
)
from app.services.synchronization_job_contract import InvalidSeasonSyncPayloadError
from tests.support.provider_sync_fixtures import SyncGraph, graph_factory
from tests.support.transactional_email import (
    assert_accepted,
    assert_rejected,
    database_session_fixture,
    make_workspace,
)

SEASON_SYNC = BackgroundJobType.LEAGUE_SEASON_SYNC
EMAIL = BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY
NOW = datetime(2030, 1, 1, tzinfo=UTC)
LATER = datetime(2030, 1, 2, tzinfo=UTC)


@pytest.fixture
def database_session() -> Iterator[Session]:
    yield from database_session_fixture()


def make_season(session: Session, *, installation_id: int | None = None) -> Season:
    league = League(
        name="Season Sync Routine League",
        installation_id=installation_id,
        provider=FantasyProvider.ESPN,
        provider_league_id=f"routine-{uuid4().hex[:12]}",
    )
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        year=2026,
        current_week=3,
        regular_season_weeks=14,
        provider_season_id="2026",
    )
    session.add(season)
    session.flush()

    return season


_DEFAULT_KEY = object()


def season_job(
    *,
    workspace_id: int | None,
    season_id: int = 777,
    payload: Any = None,
    payload_version: int = 1,
    idempotency_key: str | None | object = _DEFAULT_KEY,
) -> BackgroundJob:
    return BackgroundJob(
        workspace_id=workspace_id,
        job_type=SEASON_SYNC,
        payload={"version": 1, "season_id": season_id} if payload is None else payload,
        payload_version=payload_version,
        idempotency_key=(
            f"league-season-sync:{season_id}"
            if idempotency_key is _DEFAULT_KEY
            else idempotency_key
        ),
    )


def active_jobs(session: Session, season_id: int) -> list[BackgroundJob]:
    return list(
        session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key == f"league-season-sync:{season_id}",
                BackgroundJob.status.in_(
                    (BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING)
                ),
            )
        )
    )


# --------------------------------------------------------------------------
# a. Enum and repository enqueue
# --------------------------------------------------------------------------


def test_job_type_value() -> None:
    assert SEASON_SYNC.value == "league_season_sync"
    assert BackgroundJobType("league_season_sync") is SEASON_SYNC


def test_repository_accepts_season_sync_without_workspace(database_session: Session) -> None:
    result = BackgroundJobRepository(database_session).enqueue(
        workspace_id=None,
        job_type=SEASON_SYNC,
        payload={"version": 1, "season_id": 777},
        idempotency_key="league-season-sync:777",
    )

    assert result.existing is False
    assert result.job.workspace_id is None
    assert result.job.job_type is SEASON_SYNC


def test_repository_accepts_season_sync_with_workspace(database_session: Session) -> None:
    workspace = make_workspace(database_session)

    result = BackgroundJobRepository(database_session).enqueue(
        workspace_id=workspace.id,
        job_type=SEASON_SYNC,
        payload={"version": 1, "season_id": 778},
        idempotency_key="league-season-sync:778",
    )

    assert result.existing is False
    assert result.job.workspace_id == workspace.id


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 1},
        {"season_id": 777},
        {"version": 1, "season_id": 777, "workspace_id": 1},
        {"version": 2, "season_id": 777},
        {"version": 1, "season_id": "777"},
        {"version": 1, "season_id": 0},
        {"version": 1, "season_id": True},
    ],
)
def test_repository_rejects_bad_season_payload(
    database_session: Session, payload: dict[str, Any]
) -> None:
    with pytest.raises(InvalidSeasonSyncPayloadError):
        BackgroundJobRepository(database_session).enqueue(
            workspace_id=None,
            job_type=SEASON_SYNC,
            payload=payload,
            idempotency_key="league-season-sync:777",
        )


def test_repository_rejects_unsupported_payload_version(database_session: Session) -> None:
    with pytest.raises(InvalidSeasonSyncPayloadError):
        BackgroundJobRepository(database_session).enqueue(
            workspace_id=None,
            job_type=SEASON_SYNC,
            payload={"version": 1, "season_id": 777},
            payload_version=2,
            idempotency_key="league-season-sync:777",
        )


@pytest.mark.parametrize(
    "key",
    [None, "", "league-season-sync:778", "season-sync:777", "league-season-sync:0777"],
)
def test_repository_requires_season_idempotency_key(
    database_session: Session, key: str | None
) -> None:
    with pytest.raises(ValueError):
        BackgroundJobRepository(database_session).enqueue(
            workspace_id=None,
            job_type=SEASON_SYNC,
            payload={"version": 1, "season_id": 777},
            idempotency_key=key,
        )


@pytest.mark.parametrize(
    "job_type",
    [
        job_type
        for job_type in BackgroundJobType
        if job_type not in (EMAIL, BackgroundJobType.LEAGUE_SEASON_SYNC)
    ],
)
def test_repository_rejects_other_job_types_without_workspace(
    database_session: Session, job_type: BackgroundJobType
) -> None:
    with pytest.raises(ValueError, match="workspace_id"):
        BackgroundJobRepository(database_session).enqueue(
            workspace_id=None, job_type=job_type, payload={"season_id": 1}
        )


# --------------------------------------------------------------------------
# b. Database constraints, bypassing the repository
# --------------------------------------------------------------------------


def test_database_accepts_exact_payload_with_or_without_workspace(
    database_session: Session,
) -> None:
    workspace = make_workspace(database_session)

    assert_accepted(database_session, season_job(workspace_id=None, season_id=801))
    assert_accepted(database_session, season_job(workspace_id=workspace.id, season_id=802))


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "season_id": 803, "workspace_id": 1},
        {"version": 1, "season_id": 803, "full_sync": False},
        {"version": 1},
        {"season_id": 803},
        {"version": 1, "season_id": "803"},
        {"version": 1, "season_id": 0},
        {"version": 1, "season_id": 1.5},
        {"version": 1, "season_id": -3},
        {"version": 2, "season_id": 803},
        {"version": "1", "season_id": 803},
        {},
        [],
    ],
)
def test_database_rejects_malformed_season_payloads(
    database_session: Session, payload: Any
) -> None:
    workspace = make_workspace(database_session)

    assert_rejected(
        database_session,
        season_job(workspace_id=None, payload=payload, idempotency_key="league-season-sync:803"),
    )
    assert_rejected(
        database_session,
        season_job(
            workspace_id=workspace.id, payload=payload, idempotency_key="league-season-sync:803"
        ),
    )


def test_database_rejects_payload_version_two(database_session: Session) -> None:
    assert_rejected(database_session, season_job(workspace_id=None, payload_version=2))


def test_database_rejects_null_idempotency_key(database_session: Session) -> None:
    assert_rejected(database_session, season_job(workspace_id=None, idempotency_key=None))


@pytest.mark.parametrize(
    "key",
    ["league-season-sync:778", "league-season-sync:", "season-sync:777", "other", ""],
)
def test_database_rejects_mismatched_idempotency_key(database_session: Session, key: str) -> None:
    assert_rejected(database_session, season_job(workspace_id=None, idempotency_key=key))


def test_legacy_and_email_jobs_remain_accepted(database_session: Session) -> None:
    workspace = make_workspace(database_session)

    assert_accepted(
        database_session,
        BackgroundJob(
            workspace_id=workspace.id,
            job_type=BackgroundJobType.INCREMENTAL_SEASON_SYNC,
            payload={"season_id": 1, "full_sync": False, "trigger": "administrator"},
        ),
    )
    assert_accepted(
        database_session,
        BackgroundJob(workspace_id=None, job_type=EMAIL, payload={"version": 1, "delivery_id": 1}),
    )


# --------------------------------------------------------------------------
# c. enqueue_season_sync
# --------------------------------------------------------------------------


def test_enqueue_creates_job_and_queues_season(database_session: Session) -> None:
    workspace = make_workspace(database_session)
    season = make_season(database_session, installation_id=workspace.id)

    result = enqueue_season_sync(database_session, season_id=season.id)

    job = result.job
    assert result.existing is False
    assert job.status is BackgroundJobStatus.PENDING
    assert job.job_type is SEASON_SYNC
    assert job.workspace_id == workspace.id
    assert job.payload == {"version": 1, "season_id": season.id}
    assert job.payload_version == 1
    assert job.idempotency_key == f"league-season-sync:{season.id}"
    assert job.priority == 25
    assert job.maximum_attempts == SEASON_SYNC_MAXIMUM_ATTEMPTS == 5
    assert season.sync_state is SeasonSyncState.QUEUED


def test_enqueue_for_league_without_workspace_has_null_workspace(
    database_session: Session,
) -> None:
    season = make_season(database_session, installation_id=None)

    result = enqueue_season_sync(database_session, season_id=season.id, priority=60)

    assert result.existing is False
    assert result.job.workspace_id is None
    assert result.job.priority == 60
    assert season.sync_state is SeasonSyncState.QUEUED


def test_repeated_enqueue_returns_the_same_active_job(database_session: Session) -> None:
    season = make_season(database_session)

    first = enqueue_season_sync(database_session, season_id=season.id)
    second = enqueue_season_sync(database_session, season_id=season.id, priority=99)

    assert first.existing is False
    assert second.existing is True
    assert second.job.id == first.job.id
    assert second.job.priority == 25
    assert len(active_jobs(database_session, season.id)) == 1


def test_admin_scheduler_and_direct_enqueue_share_one_job(database_session: Session) -> None:
    season_id = 1

    admin = enqueue_administrator_synchronization(database_session, workspace_id=1, full_sync=True)
    scheduled = enqueue_scheduled_synchronization(
        database_session,
        target=ScheduledSynchronizationTarget(workspace_id=1, season_id=season_id),
        scheduled_for=NOW,
    )
    direct = enqueue_season_sync(database_session, season_id=season_id)

    assert admin.existing is False
    assert scheduled.existing is True
    assert direct.existing is True
    assert admin.job_id == scheduled.job_id == direct.job.id
    assert len(active_jobs(database_session, season_id)) == 1
    assert ADMIN_SYNC_PRIORITY > SCHEDULED_SYNC_PRIORITY


@pytest.mark.parametrize(
    "terminal",
    [BackgroundJobStatus.SUCCEEDED, BackgroundJobStatus.FAILED, BackgroundJobStatus.CANCELED],
)
def test_terminal_job_does_not_block_a_new_one(
    database_session: Session, terminal: BackgroundJobStatus
) -> None:
    season = make_season(database_session)
    first = enqueue_season_sync(database_session, season_id=season.id).job
    first.status = terminal

    if terminal is BackgroundJobStatus.SUCCEEDED:
        first.completed_at = NOW
    elif terminal is BackgroundJobStatus.FAILED:
        first.failed_at = NOW
    else:
        first.canceled_at = NOW

    database_session.flush()

    second = enqueue_season_sync(database_session, season_id=season.id)

    assert second.existing is False
    assert second.job.id != first.id
    assert [job.id for job in active_jobs(database_session, season.id)] == [second.job.id]


def test_enqueue_does_not_overwrite_syncing_state(database_session: Session) -> None:
    season = make_season(database_session)
    season.sync_state = SeasonSyncState.SYNCING
    database_session.flush()

    result = enqueue_season_sync(database_session, season_id=season.id)

    assert result.existing is False
    assert season.sync_state is SeasonSyncState.SYNCING


def test_enqueue_does_not_change_state_when_job_exists(database_session: Session) -> None:
    season = make_season(database_session)
    enqueue_season_sync(database_session, season_id=season.id)
    season.sync_state = SeasonSyncState.SYNCING
    database_session.flush()

    result = enqueue_season_sync(database_session, season_id=season.id)

    assert result.existing is True
    assert season.sync_state is SeasonSyncState.SYNCING


def test_enqueue_missing_season_raises_without_job(database_session: Session) -> None:
    missing = 2_000_000_000
    before = database_session.scalar(select(func.count()).select_from(BackgroundJob))

    with pytest.raises(SeasonSyncSeasonNotFoundError):
        enqueue_season_sync(database_session, season_id=missing)

    assert database_session.scalar(select(func.count()).select_from(BackgroundJob)) == before
    assert issubclass(SeasonSyncSeasonNotFoundError, LookupError)


# --------------------------------------------------------------------------
# d. Season state transitions (transient objects)
# --------------------------------------------------------------------------


def transient_season(**values: Any) -> Season:
    fields: dict[str, Any] = {"data_version": 0, "sync_state": None}
    fields.update(values)

    return Season(**fields)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, SeasonSyncState.QUEUED),
        (SeasonSyncState.QUEUED, SeasonSyncState.QUEUED),
        (SeasonSyncState.FRESH, SeasonSyncState.QUEUED),
        (SeasonSyncState.STALE, SeasonSyncState.QUEUED),
        (SeasonSyncState.FAILED, SeasonSyncState.QUEUED),
        (SeasonSyncState.SYNCING, SeasonSyncState.SYNCING),
    ],
)
def test_mark_queued(before: SeasonSyncState | None, after: SeasonSyncState) -> None:
    season = transient_season(sync_state=before, data_version=3)

    mark_queued(season)

    assert season.sync_state is after
    assert season.data_version == 3


@pytest.mark.parametrize(
    "before", [None, *SeasonSyncState], ids=lambda state: getattr(state, "value", "none")
)
def test_mark_syncing(before: SeasonSyncState | None) -> None:
    season = transient_season(sync_state=before, data_version=2, last_sync_failure_code="x")

    mark_syncing(season, attempt_at=NOW)

    assert season.sync_state is SeasonSyncState.SYNCING
    assert season.last_sync_attempt_at == NOW
    assert season.data_version == 2
    assert season.last_successful_sync_at is None
    assert season.last_sync_failure_code == "x"


@pytest.mark.parametrize(
    "before", [None, *SeasonSyncState], ids=lambda s: getattr(s, "value", "none")
)
def test_mark_succeeded(before: SeasonSyncState | None) -> None:
    season = transient_season(
        sync_state=before,
        data_version=4,
        last_sync_failure_code="invalid_provider_data",
        last_sync_attempt_at=NOW,
    )

    mark_succeeded(season, completed_at=LATER)

    assert season.data_version == 5
    assert season.sync_state is SeasonSyncState.FRESH
    assert season.last_successful_sync_at == LATER
    assert season.last_synced_at == LATER
    assert season.last_sync_failure_code is None
    assert season.last_sync_attempt_at == NOW


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"data_version": 1}, SeasonSyncState.STALE),
        ({"last_successful_sync_at": NOW}, SeasonSyncState.STALE),
        ({"last_synced_at": NOW}, SeasonSyncState.STALE),
        ({"sync_state": SeasonSyncState.SYNCING, "data_version": 2}, SeasonSyncState.STALE),
        ({"sync_state": SeasonSyncState.FRESH, "data_version": 2}, SeasonSyncState.STALE),
        ({"sync_state": None, "last_synced_at": NOW}, SeasonSyncState.STALE),
        ({"sync_state": None}, SeasonSyncState.FAILED),
        ({"sync_state": SeasonSyncState.SYNCING}, SeasonSyncState.FAILED),
        ({"sync_state": SeasonSyncState.QUEUED}, SeasonSyncState.FAILED),
        ({"sync_state": SeasonSyncState.FRESH}, SeasonSyncState.FAILED),
    ],
)
def test_mark_failed_state_depends_on_prior_data(
    values: dict[str, Any], expected: SeasonSyncState
) -> None:
    season = transient_season(**values)
    version_before = season.data_version

    mark_failed(season, failure_code="provider_unavailable")

    assert season.sync_state is expected
    assert season.last_sync_failure_code == "provider_unavailable"
    assert season.data_version == version_before


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, False),
        ({"data_version": 1}, True),
        ({"last_successful_sync_at": NOW}, True),
        ({"last_synced_at": NOW}, True),
    ],
)
def test_has_successful_data(values: dict[str, Any], expected: bool) -> None:
    assert has_successful_data(transient_season(**values)) is expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("provider_unavailable", "provider_unavailable"),
        ("a", "a"),
        ("a" * 64, "a" * 64),
        ("a1_b2", "a1_b2"),
        ("Bad Code", GENERIC_SYNC_FAILURE_CODE),
        ("Upper", GENERIC_SYNC_FAILURE_CODE),
        ("1leading", GENERIC_SYNC_FAILURE_CODE),
        ("_leading", GENERIC_SYNC_FAILURE_CODE),
        ("has-dash", GENERIC_SYNC_FAILURE_CODE),
        ("x" * 65, GENERIC_SYNC_FAILURE_CODE),
        ("", GENERIC_SYNC_FAILURE_CODE),
        (None, GENERIC_SYNC_FAILURE_CODE),
    ],
)
def test_safe_failure_code_is_bounded(code: str | None, expected: str) -> None:
    assert safe_failure_code(code) == expected
    assert GENERIC_SYNC_FAILURE_CODE == "sync_failed"


def test_mark_failed_bounds_unsafe_code() -> None:
    season = transient_season()

    mark_failed(season, failure_code="Bad Code")

    assert season.last_sync_failure_code == "sync_failed"


# --------------------------------------------------------------------------
# e. Lease recovery
# --------------------------------------------------------------------------


def lease_job(season_id: int, job_type: BackgroundJobType = SEASON_SYNC) -> BackgroundJob:
    return BackgroundJob(
        workspace_id=None,
        job_type=job_type,
        payload={"version": 1, "season_id": season_id},
        payload_version=1,
    )


def running_run(session: Session, season: Season) -> SyncRun:
    run = SyncRun(
        season_id=season.id,
        provider=FantasyProvider.ESPN,
        status=SyncStatus.RUNNING,
        is_full_sync=True,
        teams_upserted=0,
        matchups_upserted=0,
        player_week_scores_upserted=0,
        analytics_rebuilt=False,
    )
    session.add(run)
    session.flush()

    return run


@pytest.mark.parametrize(
    "job_type", [job_type for job_type in BackgroundJobType if job_type is not SEASON_SYNC]
)
def test_lease_recovery_ignores_other_job_types(
    database_session: Session, job_type: BackgroundJobType
) -> None:
    season = make_season(database_session)
    season.sync_state = SeasonSyncState.SYNCING
    run = running_run(database_session, season)

    apply_season_sync_lease_recovery(
        database_session, job=lease_job(season.id, job_type), retrying=True
    )

    database_session.refresh(run)
    assert season.sync_state is SeasonSyncState.SYNCING
    assert run.status is SyncStatus.RUNNING


@pytest.mark.parametrize(
    "state",
    [
        None,
        SeasonSyncState.QUEUED,
        SeasonSyncState.FRESH,
        SeasonSyncState.STALE,
        SeasonSyncState.FAILED,
    ],
    ids=lambda s: getattr(s, "value", "none"),
)
@pytest.mark.parametrize("retrying", [True, False])
def test_lease_recovery_ignores_non_syncing_seasons(
    database_session: Session, state: SeasonSyncState | None, retrying: bool
) -> None:
    season = make_season(database_session)
    season.sync_state = state
    season.last_sync_failure_code = None
    run = running_run(database_session, season)
    database_session.flush()

    apply_season_sync_lease_recovery(database_session, job=lease_job(season.id), retrying=retrying)

    database_session.refresh(run)
    assert season.sync_state is state
    assert season.last_sync_failure_code is None
    assert run.status is SyncStatus.RUNNING


def test_lease_recovery_ignores_invalid_payload(database_session: Session) -> None:
    season = make_season(database_session)
    season.sync_state = SeasonSyncState.SYNCING
    job = lease_job(season.id)
    job.payload = {"version": 1, "season_id": season.id, "workspace_id": 1}

    apply_season_sync_lease_recovery(database_session, job=job, retrying=True)

    assert season.sync_state is SeasonSyncState.SYNCING


def test_lease_recovery_retrying_requeues_and_fails_running_run(
    database_session: Session,
) -> None:
    season = make_season(database_session)
    season.sync_state = SeasonSyncState.SYNCING
    running = running_run(database_session, season)
    finished = running_run(database_session, season)
    finished.status = SyncStatus.SUCCEEDED
    finished.completed_at = NOW
    database_session.flush()

    apply_season_sync_lease_recovery(database_session, job=lease_job(season.id), retrying=True)

    database_session.refresh(running)
    database_session.refresh(finished)
    assert season.sync_state is SeasonSyncState.QUEUED
    assert season.last_sync_failure_code is None
    assert running.status is SyncStatus.FAILED
    assert running.completed_at is not None
    assert running.safe_error_message == LEASE_EXPIRED_SYNC_MESSAGE
    assert running.analytics_rebuilt is False
    assert finished.status is SyncStatus.SUCCEEDED
    assert finished.safe_error_message is None


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"data_version": 2, "last_successful_sync_at": NOW}, SeasonSyncState.STALE),
        ({"data_version": 0}, SeasonSyncState.FAILED),
    ],
    ids=["prior-data", "no-prior-data"],
)
def test_lease_recovery_exhausted_marks_stale_or_failed(
    database_session: Session, values: dict[str, Any], expected: SeasonSyncState
) -> None:
    season = make_season(database_session)
    season.sync_state = SeasonSyncState.SYNCING

    for name, value in values.items():
        setattr(season, name, value)

    run = running_run(database_session, season)
    database_session.flush()
    version_before = season.data_version

    apply_season_sync_lease_recovery(database_session, job=lease_job(season.id), retrying=False)

    database_session.refresh(run)
    assert EXPIRED_LEASE_SYNC_CODE == "worker_lease_expired"
    assert season.sync_state is expected
    assert season.last_sync_failure_code == "worker_lease_expired"
    assert season.data_version == version_before
    assert run.status is SyncStatus.FAILED
    assert run.safe_error_message == LEASE_EXPIRED_SYNC_MESSAGE


# --------------------------------------------------------------------------
# f. Safe status serialization through the admin API
# --------------------------------------------------------------------------

client = TestClient(app)

STATUS_KEYS = {
    "provider",
    "season_id",
    "provider_league_id",
    "provider_season_id",
    "last_synced_at",
    "latest_run",
}
RUN_KEYS = {
    "id",
    "status",
    "is_full_sync",
    "started_at",
    "completed_at",
    "requested_from_week",
    "requested_through_week",
    "teams_upserted",
    "matchups_upserted",
    "player_week_scores_upserted",
    "safe_error_message",
}


@pytest.fixture
def make_graph() -> Iterator[Callable[[], SyncGraph]]:
    with graph_factory() as create:
        yield create


def admin_user(workspace_id: int) -> Callable[[], User]:
    def override() -> User:
        return User(
            id=123_457,
            username="season-sync-routine-admin",
            display_name="Season Sync Routine",
            workspace_id=workspace_id,
            email=None,
            password_hash="$argon2id$test-placeholder",
            role=UserRole.ADMIN,
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    return override


def test_sync_status_response_shape_is_unchanged(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    app.dependency_overrides[get_current_user] = admin_user(graph.workspace_id)

    try:
        response = client.get("/api/admin/sync-status")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()
    assert set(payload) == STATUS_KEYS
    assert payload["provider"] == "espn"
    assert payload["season_id"] == graph.season_id
    assert payload["provider_league_id"] == graph.provider_league_id
    assert payload["latest_run"] is not None
    assert set(payload["latest_run"]) == RUN_KEYS


def test_admin_sync_response_shape_and_season_job(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    app.dependency_overrides[get_current_user] = admin_user(graph.workspace_id)
    app.dependency_overrides[require_session_csrf] = lambda: None
    job_id: int | None = None

    try:
        response = client.post(
            "/api/admin/sync",
            headers={"Origin": "http://localhost:5173"},
            json={"full_sync": False},
        )
        job_id = response.json().get("job_id")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_session_csrf, None)

    try:
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert set(response.json()) == {"job_id", "status", "existing"}
        assert response.json()["status"] == "pending"
        assert response.json()["existing"] is False

        with SessionFactory() as session:
            job = session.get(BackgroundJob, job_id)
            assert job is not None
            assert job.job_type is SEASON_SYNC
            assert job.workspace_id == graph.workspace_id
            assert job.payload == {"version": 1, "season_id": graph.season_id}
            assert job.priority == ADMIN_SYNC_PRIORITY
    finally:
        if job_id is not None:
            with SessionFactory() as session:
                job = session.get(BackgroundJob, job_id)

                if job is not None:
                    session.delete(job)
                    session.commit()
