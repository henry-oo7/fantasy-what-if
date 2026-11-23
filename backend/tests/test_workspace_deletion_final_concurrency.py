"""FR-20 final deletion races on separate real PostgreSQL connections.

Each actor uses its own ``SessionFactory`` session, so every actor is a
distinct connection and transaction. Time is injected explicitly.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from app.db.auth_models import AuthSessionPurpose, User
from app.db.platform_enums import (
    BackgroundJobStatus,
    FantasyProvider,
    InstallationStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.provider_league_claim_repository import (
    ProviderLeagueAlreadyClaimedError,
    ProviderLeagueClaimRepository,
)
from app.repositories.session_repository import SessionRepository
from app.repositories.workspace_deletion_repository import WorkspaceDeletionRepository
from app.services.background_job_worker import (
    BackgroundJobDeferredError,
    BackgroundJobSnapshot,
    snapshot_background_job,
)
from app.services.workspace_deletion import (
    WorkspaceDeletionUnavailableError,
    cancel_workspace_deletion,
)
from app.services.workspace_deletion_execution import (
    WorkspaceDeletionJobHandler,
    WorkspaceDeletionOutcomeKind,
    execute_workspace_deletion,
)
from tests.support.workspace_deletion_graph import (
    ADMIN_PASSWORD,
    DeletionGraph,
    cleanup_graphs,
    create_deletion_graph,
    deletion_records,
    enqueue_deletion_job,
    load_job,
    load_workspace,
    owned_row_counts,
)

WORKER = "final-deletion-race-worker"
LEASE = timedelta(minutes=5)


@pytest.fixture
def graph() -> Iterator[DeletionGraph]:
    created = create_deletion_graph()
    yield created
    cleanup_graphs([created.workspace_id])


def claim(job_id: int, *, now: datetime) -> BackgroundJobSnapshot:
    with SessionFactory() as session:
        job = BackgroundJobRepository(session).claim_next_available(
            worker_id=WORKER, lease_duration=LEASE, now=now
        )
        assert job is not None and job.id == job_id
        snapshot = snapshot_background_job(job)
        session.commit()

    return snapshot


def recovery_session_id(graph: DeletionGraph, *, now: datetime) -> int:
    with SessionFactory() as session:
        user = session.get(User, graph.administrator_id)
        created = SessionRepository(session).create_session(
            user=user,
            lifetime=timedelta(minutes=15),
            purpose=AuthSessionPurpose.DELETION_RECOVERY,
            now=now - timedelta(minutes=1),
        )
        session.commit()

        return created.session.id


def cancel(graph: DeletionGraph, recovery_id: int, now: datetime) -> str:
    with SessionFactory() as session:
        cancel_workspace_deletion(
            session,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            auth_session_id=recovery_id,
            current_password=ADMIN_PASSWORD,
            now=now,
        )
        session.commit()

    return "canceled"


def delete(snapshot: BackgroundJobSnapshot, now: datetime) -> WorkspaceDeletionOutcomeKind:
    with SessionFactory() as session:
        outcome = execute_workspace_deletion(
            session,
            workspace_id=snapshot.workspace_id,
            job_id=snapshot.id,
            attempt_count=snapshot.attempt_count,
            now=now,
        )
        session.commit()

    return outcome


def run_concurrently(*actors: Callable[[], object]) -> list[object]:
    barrier = threading.Barrier(len(actors))

    def synchronized(actor: Callable[[], object]) -> object:
        barrier.wait(timeout=10)
        try:
            return actor()
        except Exception as exc:  # noqa: BLE001 - outcomes are asserted
            return exc

    with ThreadPoolExecutor(max_workers=len(actors)) as executor:
        return list(executor.map(synchronized, actors))


def assert_deleted(graph: DeletionGraph) -> None:
    assert load_workspace(graph.workspace_id) is None
    assert len(deletion_records(graph.workspace_id)) == 1
    assert all(count == 0 for count in owned_row_counts(graph).values())


def assert_restored(graph: DeletionGraph) -> None:
    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.ACTIVE
    assert deletion_records(graph.workspace_id) == []


# Deterministic lock-order winners.


def test_cancellation_holding_the_lock_before_cutoff_wins(graph: DeletionGraph) -> None:
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)
    before_cutoff = graph.scheduled_for - timedelta(microseconds=1)
    recovery_id = recovery_session_id(graph, now=before_cutoff)

    with SessionFactory() as canceler:
        cancel_workspace_deletion(
            canceler,
            workspace_id=graph.workspace_id,
            user_id=graph.administrator_id,
            auth_session_id=recovery_id,
            current_password=ADMIN_PASSWORD,
            now=before_cutoff,
        )
        # The deletion transaction blocks on the workspace row lock.
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(delete, snapshot, graph.scheduled_for)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.5)
            canceler.commit()
            outcome = pending.result(timeout=10)

    assert outcome is WorkspaceDeletionOutcomeKind.NOT_PENDING
    assert_restored(graph)


def test_deletion_holding_the_lock_at_cutoff_wins(graph: DeletionGraph) -> None:
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)
    before_cutoff = graph.scheduled_for - timedelta(microseconds=1)
    recovery_id = recovery_session_id(graph, now=before_cutoff)

    with SessionFactory() as deleter:
        execute_workspace_deletion(
            deleter,
            workspace_id=graph.workspace_id,
            job_id=snapshot.id,
            attempt_count=snapshot.attempt_count,
            now=graph.scheduled_for,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            # Even a skewed pre-cutoff cancellation waits for the lock.
            pending = executor.submit(cancel, graph, recovery_id, before_cutoff)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.5)
            deleter.commit()
            with pytest.raises(WorkspaceDeletionUnavailableError):
                pending.result(timeout=10)

    assert_deleted(graph)


# Random interleavings on separate connections.


@pytest.mark.parametrize("round_number", range(3))
@pytest.mark.parametrize(
    ("cancel_offset", "delete_offset", "expected"),
    [
        # Before cutoff: cancellation wins and deletion never runs early.
        (timedelta(seconds=-1), timedelta(seconds=-1), "canceled"),
        # At the exact cutoff deletion wins.
        (timedelta(0), timedelta(0), "deleted"),
        # After the cutoff deletion wins.
        (timedelta(seconds=1), timedelta(seconds=1), "deleted"),
        # Skewed clocks: one side wins, never both.
        (timedelta(seconds=-1), timedelta(0), "either"),
    ],
)
def test_cancellation_versus_deletion_race(
    graph: DeletionGraph,
    cancel_offset: timedelta,
    delete_offset: timedelta,
    expected: str,
    round_number: int,
) -> None:
    del round_number
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)
    cancel_now = graph.scheduled_for + cancel_offset
    delete_now = graph.scheduled_for + delete_offset
    recovery_id = recovery_session_id(graph, now=cancel_now)

    cancel_result, delete_result = run_concurrently(
        lambda: cancel(graph, recovery_id, cancel_now),
        lambda: delete(snapshot, delete_now),
    )

    canceled = cancel_result == "canceled"
    deleted = delete_result is WorkspaceDeletionOutcomeKind.DELETED
    assert canceled != deleted, (cancel_result, delete_result)

    if expected == "canceled":
        assert canceled
        assert isinstance(delete_result, BackgroundJobDeferredError) or (
            delete_result is WorkspaceDeletionOutcomeKind.NOT_PENDING
        )
    elif expected == "deleted":
        assert deleted
        assert isinstance(cancel_result, WorkspaceDeletionUnavailableError)

    if canceled:
        assert_restored(graph)
    else:
        assert_deleted(graph)


@pytest.mark.parametrize("round_number", range(3))
def test_duplicate_deletion_executions_are_idempotent(
    graph: DeletionGraph,
    round_number: int,
) -> None:
    del round_number
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)

    results = run_concurrently(
        lambda: delete(snapshot, graph.scheduled_for),
        lambda: delete(snapshot, graph.scheduled_for),
    )

    assert sorted(str(result) for result in results) == ["already_completed", "deleted"]
    assert_deleted(graph)


# Release only after commit.


def test_provider_league_claimable_only_after_deletion_commits(graph: DeletionGraph) -> None:
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)
    claimant_ready = threading.Event()

    def claim_same_league() -> int:
        with SessionFactory() as session:
            other = Installation(
                application_name="Claimant Workspace",
                timezone="America/Chicago",
                status=InstallationStatus.ACTIVE,
            )
            session.add(other)
            session.flush()
            claimant_ready.set()
            claim = ProviderLeagueClaimRepository(session).create_claim(
                workspace_id=other.id,
                provider=FantasyProvider.ESPN,
                provider_league_id=graph.provider_league_id,
            )
            claim_id = claim.id
            session.rollback()

            return claim_id

    with SessionFactory() as deleter:
        execute_workspace_deletion(
            deleter,
            workspace_id=graph.workspace_id,
            job_id=snapshot.id,
            attempt_count=snapshot.attempt_count,
            now=graph.scheduled_for,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(claim_same_league)
            assert claimant_ready.wait(timeout=10)
            # The uncommitted delete keeps the identity reserved.
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.5)
            deleter.commit()
            assert pending.result(timeout=10) > 0

    assert_deleted(graph)


def test_provider_league_stays_reserved_when_deletion_rolls_back(graph: DeletionGraph) -> None:
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)

    def claim_same_league() -> object:
        with SessionFactory() as session:
            other = Installation(
                application_name="Claimant Workspace",
                timezone="America/Chicago",
                status=InstallationStatus.ACTIVE,
            )
            session.add(other)
            session.flush()
            try:
                ProviderLeagueClaimRepository(session).create_claim(
                    workspace_id=other.id,
                    provider=FantasyProvider.ESPN,
                    provider_league_id=graph.provider_league_id,
                )
            except ProviderLeagueAlreadyClaimedError as exc:
                return exc
            finally:
                session.rollback()

            return "claimed"

    with SessionFactory() as deleter:
        execute_workspace_deletion(
            deleter,
            workspace_id=graph.workspace_id,
            job_id=snapshot.id,
            attempt_count=snapshot.attempt_count,
            now=graph.scheduled_for,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(claim_same_league)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.5)
            deleter.rollback()
            assert isinstance(pending.result(timeout=10), ProviderLeagueAlreadyClaimedError)

    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    assert deletion_records(graph.workspace_id) == []


# Lease recovery.


def recover_job(job_id: int, *, now: datetime) -> BackgroundJobStatus | None:
    """Run the recovery scan's units against this job only."""

    with SessionFactory() as session:
        repository = BackgroundJobRepository(session)
        expired = {job.id: job for job in repository.list_expired_running_jobs(now=now)}
        job = expired.get(job_id)
        status = (
            repository.recover_expired_lease(job, retry_at=now + timedelta(seconds=30), now=now)
            if job is not None
            else None
        )
        session.commit()

    return status


def test_expired_lease_with_attempts_remaining_requeues_then_deletes(
    graph: DeletionGraph,
) -> None:
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)
    expired_at = graph.scheduled_for + LEASE + timedelta(seconds=1)

    assert recover_job(job_id, now=expired_at) is BackgroundJobStatus.RETRY_SCHEDULED
    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING

    # The worker whose lease expired can no longer delete.
    handler = WorkspaceDeletionJobHandler(session_factory=SessionFactory, now=lambda: expired_at)
    with pytest.raises(Exception, match="no longer claimed"):
        handler(snapshot)
    assert deletion_records(graph.workspace_id) == []

    retry_at = expired_at + timedelta(seconds=30)
    reclaimed = claim(job_id, now=retry_at)
    assert reclaimed.attempt_count == 2
    WorkspaceDeletionJobHandler(session_factory=SessionFactory, now=lambda: retry_at)(reclaimed)
    assert_deleted(graph)


def test_expired_lease_with_attempts_exhausted_leaves_workspace_pending(
    graph: DeletionGraph,
) -> None:
    job_id = enqueue_deletion_job(graph, maximum_attempts=1)
    claim(job_id, now=graph.scheduled_for)
    expired_at = graph.scheduled_for + LEASE + timedelta(seconds=1)

    assert recover_job(job_id, now=expired_at) is BackgroundJobStatus.FAILED

    job = load_job(job_id)
    assert job is not None and job.safe_error_code == "worker_lease_expired"
    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    assert deletion_records(graph.workspace_id) == []


def test_lease_recovery_skips_a_job_locked_by_in_flight_deletion(graph: DeletionGraph) -> None:
    job_id = enqueue_deletion_job(graph)
    claim(job_id, now=graph.scheduled_for)
    expired_at = graph.scheduled_for + LEASE + timedelta(seconds=1)

    with SessionFactory() as deleter:
        repository = WorkspaceDeletionRepository(deleter)
        assert repository.lock_workspace(graph.workspace_id) is not None
        assert repository.lock_job(job_id) is not None

        assert recover_job(job_id, now=expired_at) is None
        deleter.rollback()

    job = load_job(job_id)
    assert job is not None and job.status is BackgroundJobStatus.RUNNING


def test_committed_deletion_is_authoritative_over_lease_recovery(graph: DeletionGraph) -> None:
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)
    assert delete(snapshot, graph.scheduled_for) is WorkspaceDeletionOutcomeKind.DELETED

    assert recover_job(job_id, now=graph.scheduled_for + LEASE * 2) is None
    assert load_job(job_id) is None
    assert_deleted(graph)
