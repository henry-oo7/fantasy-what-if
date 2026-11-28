"""FR-20 final workspace deletion: handler, ordered transaction, worker.

Every test commits through ``SessionFactory`` against the guarded test
database and controls time explicitly; the fixture schedule lies 29 days
in the real future, so no leftover job is due for unrelated tests.
"""

from __future__ import annotations

import ast
import inspect
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.auth.authentication import (
    InvalidCredentialsError,
    log_in_by_email,
    log_in_workspace_spectator,
)
from app.db.auth_models import User, UserRole
from app.db.models import Season, SyncRun
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    InstallationStatus,
    WorkspaceDeletionOutcome,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    WorkspaceDeletionRecord,
)
from app.db.session import SessionFactory
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.provider_league_claim_repository import (
    ProviderLeagueAlreadyClaimedError,
    ProviderLeagueClaimRepository,
)
from app.repositories.workspace_deletion_repository import WorkspaceDeletionRepository
from app.services import workspace_deletion_email, workspace_deletion_execution
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    BackgroundJobSnapshot,
    execute_one_background_job,
    snapshot_background_job,
)
from app.services.workspace_deletion_execution import (
    WorkspaceDeletionJobHandler,
    WorkspaceDeletionOutcomeKind,
    execute_workspace_deletion,
)
from tests.support.workspace_deletion_graph import (
    ADMIN_PASSWORD,
    SPECTATOR_PASSWORD,
    DeletionGraph,
    create_deletion_graph,
    deletion_recipient_cipher,
    deletion_records,
    enqueue_deletion_job,
    load_job,
    load_workspace,
    owned_row_counts,
)

WORKER = "final-deletion-worker"
LEASE = timedelta(minutes=5)


@pytest.fixture
def graphs() -> Iterator[Callable[..., DeletionGraph]]:
    from tests.support.workspace_deletion_graph import cleanup_graphs

    created: list[int] = []

    def create(**kwargs) -> DeletionGraph:
        graph = create_deletion_graph(**kwargs)
        created.append(graph.workspace_id)
        return graph

    yield create

    cleanup_graphs(created)


def claim(job_id: int, *, now: datetime) -> BackgroundJobSnapshot:
    with SessionFactory() as session:
        job = BackgroundJobRepository(session).claim_next_available(
            worker_id=WORKER, lease_duration=LEASE, now=now
        )
        assert job is not None and job.id == job_id
        snapshot = snapshot_background_job(job)
        session.commit()

    return snapshot


def run_worker(now: datetime, handler: Callable | None = None, **kwargs):
    return execute_one_background_job(
        session_factory=SessionFactory,
        worker_id=WORKER,
        lease_duration=LEASE,
        handlers={
            BackgroundJobType.WORKSPACE_DELETION: handler
            or WorkspaceDeletionJobHandler(
                session_factory=SessionFactory,
                recipient_cipher_factory=deletion_recipient_cipher,
                now=lambda: now,
            )
        },
        now=lambda: now,
        jitter_ratio=0,
        **kwargs,
    )


def assert_graph_intact(graph: DeletionGraph, before: dict[str, int]) -> None:
    after = owned_row_counts(graph)
    # The deletion job's own row may change status but never disappears.
    assert {k: v for k, v in after.items()} == before
    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    assert workspace.deletion_scheduled_for == graph.scheduled_for
    with SessionFactory() as session:
        connection = session.get(ProviderConnection, graph.connection_id)
        assert connection is not None and connection.encrypted_credentials == graph.ciphertext
    assert deletion_records(graph.workspace_id) == []


def assert_graph_removed(graph: DeletionGraph) -> None:
    assert all(count == 0 for count in owned_row_counts(graph).values()), owned_row_counts(graph)


# Registration, claimability, and payload contract.


def test_due_deletion_job_is_claimable_only_from_its_schedule(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)

    with SessionFactory() as session:
        early = BackgroundJobRepository(session).claim_next_available(
            worker_id=WORKER, lease_duration=LEASE, now=graph.scheduled_for - timedelta(seconds=1)
        )
        assert early is None or early.id != job_id
        session.rollback()

    snapshot = claim(job_id, now=graph.scheduled_for)
    assert snapshot.job_type is BackgroundJobType.WORKSPACE_DELETION
    assert snapshot.payload == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "a@example.com"},
        {"slug": "x"},
        {"credentials": "x"},
        {"encrypted_credentials": "gAAAA"},
        {"provider_league_id": "123"},
        {"user_id": 1},
        {"claim_id": 1},
        {"instructions": "delete everything"},
        {"workspace_id": 1},
    ],
)
def test_payload_allowlist_rejects_any_field(payload: dict) -> None:
    job = BackgroundJobSnapshot(
        id=1,
        workspace_id=1,
        job_type=BackgroundJobType.WORKSPACE_DELETION,
        payload_version=1,
        payload=payload,
        attempt_count=1,
        maximum_attempts=5,
    )
    handler = WorkspaceDeletionJobHandler(
        session_factory=SessionFactory, recipient_cipher_factory=deletion_recipient_cipher
    )

    with pytest.raises(BackgroundJobExecutionError) as error:
        handler(job)

    assert error.value.safe_error_code == "invalid_job_payload"
    assert error.value.retryable is False


def test_unsupported_payload_version_is_terminal() -> None:
    job = BackgroundJobSnapshot(1, 1, BackgroundJobType.WORKSPACE_DELETION, 2, {}, 1, 5)

    with pytest.raises(BackgroundJobExecutionError) as error:
        WorkspaceDeletionJobHandler(
            session_factory=SessionFactory, recipient_cipher_factory=deletion_recipient_cipher
        )(job)

    assert error.value.safe_error_code == "unsupported_payload_version"


# Preconditions and safe no-ops.


def test_early_job_defers_to_schedule_without_spending_an_attempt(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph, maximum_attempts=1)
    before = owned_row_counts(graph)
    early = graph.scheduled_for - timedelta(seconds=1)

    # The claim clock reached the schedule but the handler clock did not
    # (for example a skewed worker clock): nothing is deleted.
    result = run_worker(
        graph.scheduled_for,
        handler=WorkspaceDeletionJobHandler(
            session_factory=SessionFactory,
            recipient_cipher_factory=deletion_recipient_cipher,
            now=lambda: early,
        ),
    )

    job = load_job(job_id)
    assert result.retry_scheduled is True and result.failed is False
    assert job.status is BackgroundJobStatus.PENDING
    assert job.attempt_count == 0
    assert job.available_at == graph.scheduled_for
    assert job.safe_error_code == "workspace_deletion_not_due"
    assert_graph_intact(graph, before)

    # With its single attempt restored, the job still completes when due.
    assert run_worker(graph.scheduled_for).succeeded
    assert_graph_removed(graph)


def test_canceled_workspace_is_a_safe_noop(graphs: Callable[..., DeletionGraph]) -> None:
    graph = graphs(pending=False)

    with SessionFactory() as session:
        outcome = execute_workspace_deletion(
            session,
            workspace_id=graph.workspace_id,
            job_id=1,
            attempt_count=1,
            now=graph.scheduled_for + timedelta(days=1),
            recipient_cipher_factory=deletion_recipient_cipher,
        )
        session.commit()

    assert outcome is WorkspaceDeletionOutcomeKind.NOT_PENDING
    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.ACTIVE
    assert deletion_records(graph.workspace_id) == []


def test_missing_workspace_with_record_is_already_completed(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)
    assert run_worker(graph.scheduled_for).succeeded

    with SessionFactory() as session:
        outcome = execute_workspace_deletion(
            session,
            workspace_id=graph.workspace_id,
            job_id=job_id,
            attempt_count=1,
            now=graph.scheduled_for,
            recipient_cipher_factory=deletion_recipient_cipher,
        )

    assert outcome is WorkspaceDeletionOutcomeKind.ALREADY_COMPLETED
    assert len(deletion_records(graph.workspace_id)) == 1


def test_missing_workspace_without_record_is_terminal() -> None:
    with SessionFactory() as session, pytest.raises(BackgroundJobExecutionError) as error:
        execute_workspace_deletion(
            session,
            workspace_id=2_000_000_000,
            job_id=1,
            attempt_count=1,
            now=datetime.now().astimezone(),
            recipient_cipher_factory=deletion_recipient_cipher,
        )

    assert error.value.safe_error_code == "workspace_deletion_workspace_missing"


def test_stale_claim_attempt_does_not_delete(graphs: Callable[..., DeletionGraph]) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)
    snapshot = claim(job_id, now=graph.scheduled_for)
    before = owned_row_counts(graph)
    stale = BackgroundJobSnapshot(
        snapshot.id,
        snapshot.workspace_id,
        snapshot.job_type,
        snapshot.payload_version,
        snapshot.payload,
        snapshot.attempt_count - 1,
        snapshot.maximum_attempts,
    )

    with pytest.raises(BackgroundJobExecutionError) as error:
        WorkspaceDeletionJobHandler(
            session_factory=SessionFactory,
            recipient_cipher_factory=deletion_recipient_cipher,
            now=lambda: graph.scheduled_for,
        )(stale)

    assert error.value.safe_error_code == "workspace_deletion_claim_lost"
    assert_graph_intact(graph, before)


# Ordered deletion, deletion record, and release.


def test_worker_deletes_complete_graph_and_recognizes_self_deleted_job(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    unrelated = graphs()
    unrelated_before = owned_row_counts(unrelated)
    job_id = enqueue_deletion_job(graph)
    before = owned_row_counts(graph)
    assert all(before[name] > 0 for name in before), before
    completed_at = graph.scheduled_for + timedelta(minutes=3)

    result = run_worker(completed_at)

    assert result.claimed and result.succeeded and not result.failed
    assert result.job_id == job_id
    assert load_job(job_id) is None
    assert_graph_removed(graph)
    assert owned_row_counts(unrelated) == unrelated_before

    [record] = deletion_records(graph.workspace_id)
    assert record.former_workspace_id == graph.workspace_id
    assert record.provider is FantasyProvider.ESPN
    assert record.normalized_provider_league_id == graph.provider_league_id
    assert record.requested_at == graph.requested_at
    assert record.scheduled_for == graph.scheduled_for
    assert record.completed_at == completed_at
    assert record.outcome is WorkspaceDeletionOutcome.COMPLETED
    assert record.retention_expires_at == completed_at + timedelta(days=365)


def test_deletion_record_stores_no_forbidden_fields(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    enqueue_deletion_job(graph)
    assert run_worker(graph.scheduled_for).succeeded

    columns = {column.name for column in WorkspaceDeletionRecord.__table__.columns}
    assert columns == {
        "id",
        "former_workspace_id",
        "provider",
        "normalized_provider_league_id",
        "requested_at",
        "scheduled_for",
        "completed_at",
        "outcome",
        "retention_expires_at",
        "created_at",
    }
    [record] = deletion_records(graph.workspace_id)
    stored = " ".join(str(getattr(record, name)) for name in columns)
    for forbidden in (
        graph.slug,
        graph.administrator_email,
        graph.display_name,
        graph.administrator_username,
        graph.spectator_username,
        graph.connection_display_name,
        graph.ciphertext,
        ADMIN_PASSWORD,
        SPECTATOR_PASSWORD,
        "argon2",
        "Traceback",
    ):
        assert forbidden not in stored


def test_leagues_and_seasons_are_gone_before_workspace_delete(
    graphs: Callable[..., DeletionGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = graphs()
    enqueue_deletion_job(graph)
    observed: dict[str, int] = {}
    original = WorkspaceDeletionRepository.delete_workspace

    def observe(self: WorkspaceDeletionRepository, workspace_id: int) -> int:
        connection_ids = select(ProviderConnection.id).where(
            ProviderConnection.installation_id == workspace_id
        )
        observed["seasons_on_connection"] = len(
            self.session.scalars(
                select(Season.id).where(Season.provider_connection_id.in_(connection_ids))
            ).all()
        )
        observed["connection_sync_runs"] = len(
            self.session.scalars(
                select(SyncRun.id).where(SyncRun.provider_connection_id.in_(connection_ids))
            ).all()
        )
        observed["records"] = len(
            self.session.scalars(
                select(WorkspaceDeletionRecord.id).where(
                    WorkspaceDeletionRecord.former_workspace_id == workspace_id
                )
            ).all()
        )
        return original(self, workspace_id)

    monkeypatch.setattr(WorkspaceDeletionRepository, "delete_workspace", observe)

    assert run_worker(graph.scheduled_for).succeeded
    assert observed == {"seasons_on_connection": 0, "connection_sync_runs": 0, "records": 1}


def test_nullable_season_sync_run_is_deleted_not_orphaned(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    enqueue_deletion_job(graph)
    assert run_worker(graph.scheduled_for).succeeded

    with SessionFactory() as session:
        remaining = session.scalars(
            select(SyncRun.id).where(SyncRun.id.in_(graph.sync_run_ids))
        ).all()

    assert remaining == []


def test_remaining_season_reference_fails_terminally_and_rolls_back(
    graphs: Callable[..., DeletionGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)
    before = owned_row_counts(graph)
    monkeypatch.setattr(
        WorkspaceDeletionRepository,
        "count_seasons_referencing_connections",
        lambda self, workspace_id: 1,
    )

    result = run_worker(graph.scheduled_for)

    job = load_job(job_id)
    assert result.failed and not result.retry_scheduled
    assert job.status is BackgroundJobStatus.FAILED
    assert job.safe_error_code == "workspace_deletion_season_reference_remaining"
    assert job.retryable is False
    assert_graph_intact(graph, {**before})


def test_integrity_failure_is_terminal_with_safe_code(
    graphs: Callable[..., DeletionGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)
    before = owned_row_counts(graph)
    original = WorkspaceDeletionRepository.add_completed_record

    def fail(self, **kwargs):
        original(self, **kwargs)
        raise IntegrityError("INSERT secret-sql", {}, Exception("raw detail"))

    monkeypatch.setattr(WorkspaceDeletionRepository, "add_completed_record", fail)

    run_worker(graph.scheduled_for)

    job = load_job(job_id)
    assert job.status is BackgroundJobStatus.FAILED
    assert job.safe_error_code == "workspace_deletion_constraint_failed"
    assert "secret-sql" not in (job.safe_error_message or "")
    assert "raw detail" not in (job.safe_error_message or "")
    assert_graph_intact(graph, before)


def test_retryable_failure_rolls_back_everything(
    graphs: Callable[..., DeletionGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)
    before = owned_row_counts(graph)
    original = WorkspaceDeletionRepository.add_completed_record

    def fail_after_deletes(self, **kwargs):
        original(self, **kwargs)
        raise OperationalError("deadlock detected", {}, Exception("raw detail"))

    monkeypatch.setattr(WorkspaceDeletionRepository, "add_completed_record", fail_after_deletes)

    result = run_worker(graph.scheduled_for)

    job = load_job(job_id)
    assert result.retry_scheduled and not result.failed
    assert job.status is BackgroundJobStatus.RETRY_SCHEDULED
    assert job.safe_error_code == "workspace_deletion_database_retryable"
    assert job.attempt_count == 1
    assert_graph_intact(graph, before)

    # The claim, slug, and email remain reserved while pending.
    assert_identity_reserved(graph)

    monkeypatch.undo()
    retry_time = job.available_at
    assert run_worker(retry_time).succeeded
    assert_graph_removed(graph)


def test_exhausted_retries_leave_workspace_pending_and_operator_can_reenqueue(
    graphs: Callable[..., DeletionGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.workspace_deletion_execution import reenqueue_workspace_deletion

    graph = graphs()
    job_id = enqueue_deletion_job(graph, maximum_attempts=1)
    before = owned_row_counts(graph)
    monkeypatch.setattr(
        WorkspaceDeletionRepository,
        "detach_leagues",
        lambda self, workspace_id: (_ for _ in ()).throw(
            OperationalError("lock timeout", {}, Exception())
        ),
    )

    result = run_worker(graph.scheduled_for)

    job = load_job(job_id)
    assert result.failed
    assert job.status is BackgroundJobStatus.FAILED
    assert job.retryable is True
    assert_graph_intact(graph, before)
    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING

    monkeypatch.undo()
    later = graph.scheduled_for + timedelta(hours=2)
    with SessionFactory() as session:
        reenqueued = reenqueue_workspace_deletion(
            session, workspace_id=graph.workspace_id, now=later
        )
        session.commit()
    assert reenqueued.existing is False and reenqueued.job_id != job_id

    with SessionFactory() as session:
        session.execute(
            BackgroundJob.__table__.update()
            .where(BackgroundJob.id == reenqueued.job_id)
            .values(priority=10_000)
        )
        session.commit()

    assert run_worker(later).succeeded
    assert_graph_removed(graph)
    assert len(deletion_records(graph.workspace_id)) == 1


def test_bookkeeping_failure_after_commit_is_recognized_as_success(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)
    real = WorkspaceDeletionJobHandler(
        session_factory=SessionFactory,
        recipient_cipher_factory=deletion_recipient_cipher,
        now=lambda: graph.scheduled_for,
    )
    calls: list[int] = []

    def commit_then_raise(job: BackgroundJobSnapshot) -> None:
        calls.append(job.id)
        real(job)
        raise RuntimeError("post-commit failure")

    result = run_worker(graph.scheduled_for, handler=commit_then_raise)

    assert result.succeeded and not result.failed
    assert calls == [job_id]
    assert load_job(job_id) is None
    assert len(deletion_records(graph.workspace_id)) == 1


def test_heartbeat_blocked_by_deletion_is_recognized_as_success(
    graphs: Callable[..., DeletionGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)
    original = WorkspaceDeletionRepository.detach_leagues

    def slow_delete(self, workspace_id: int) -> int:
        time.sleep(0.5)  # let the heartbeat block on the locked job row
        return original(self, workspace_id)

    monkeypatch.setattr(WorkspaceDeletionRepository, "detach_leagues", slow_delete)

    result = run_worker(graph.scheduled_for, heartbeat_interval=timedelta(milliseconds=50))

    assert result.succeeded
    assert load_job(job_id) is None
    assert len(deletion_records(graph.workspace_id)) == 1


def test_missing_job_without_record_still_raises(
    graphs: Callable[..., DeletionGraph],
) -> None:
    from app.services.background_job_worker import BackgroundJobOutcomePersistenceError

    graph = graphs()
    job_id = enqueue_deletion_job(graph)

    def delete_job_only(job: BackgroundJobSnapshot) -> None:
        with SessionFactory() as session:
            session.execute(BackgroundJob.__table__.delete().where(BackgroundJob.id == job_id))
            session.commit()

    with pytest.raises(BackgroundJobOutcomePersistenceError):
        run_worker(graph.scheduled_for, handler=delete_job_only)

    assert deletion_records(graph.workspace_id) == []
    assert load_workspace(graph.workspace_id) is not None


# Release of provider claim, slug, email, connection, and credentials.


def assert_identity_reserved(graph: DeletionGraph) -> None:
    with SessionFactory() as session:
        other = Installation(
            application_name="Reuse Probe",
            timezone="America/Chicago",
            status=InstallationStatus.ACTIVE,
        )
        session.add(other)
        session.flush()
        with pytest.raises(ProviderLeagueAlreadyClaimedError):
            ProviderLeagueClaimRepository(session).create_claim(
                workspace_id=other.id,
                provider=FantasyProvider.ESPN,
                provider_league_id=graph.provider_league_id,
            )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                Installation(
                    application_name="Slug Probe",
                    slug=graph.slug,
                    normalized_slug=graph.slug,
                    timezone="America/Chicago",
                    status=InstallationStatus.ACTIVE,
                )
            )
            session.flush()
        # Bypass the application pre-check: the database must reserve it.
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                User(
                    username=f"email-probe-{graph.workspace_id}",
                    display_name="Email Probe",
                    email=graph.administrator_email,
                    normalized_email=graph.administrator_email,
                    password_hash="$argon2id$probe-placeholder",
                    role=UserRole.ADMIN,
                    workspace_id=other.id,
                )
            )
            session.flush()
        session.rollback()


def test_claim_slug_email_and_credentials_release_together(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    enqueue_deletion_job(graph)
    assert_identity_reserved(graph)

    assert run_worker(graph.scheduled_for).succeeded

    with SessionFactory() as session:
        assert session.get(ProviderConnection, graph.connection_id) is None
        assert (
            session.scalar(
                select(ProviderConnection.id).where(
                    ProviderConnection.encrypted_credentials == graph.ciphertext
                )
            )
            is None
        )

        reuse = Installation(
            application_name="Reuse Workspace",
            slug=graph.slug,
            normalized_slug=graph.slug,
            timezone="America/Chicago",
            status=InstallationStatus.ACTIVE,
        )
        session.add(reuse)
        session.flush()
        ProviderLeagueClaimRepository(session).create_claim(
            workspace_id=reuse.id,
            provider=FantasyProvider.ESPN,
            provider_league_id=graph.provider_league_id,
        )
        AuthenticationRepository(session).create_user(
            username="reuse-admin-probe",
            display_name="Reuse Admin",
            email=graph.administrator_email,
            password=ADMIN_PASSWORD,
            role=UserRole.ADMIN,
            workspace_id=reuse.id,
        )
        session.flush()
        session.rollback()


def test_logins_fail_generically_after_final_deletion(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    enqueue_deletion_job(graph)
    assert run_worker(graph.scheduled_for).succeeded

    with SessionFactory() as session:
        with pytest.raises(InvalidCredentialsError):
            log_in_by_email(
                session,
                email=graph.administrator_email,
                password=ADMIN_PASSWORD,
                session_lifetime_hours=8,
            )
        with pytest.raises(InvalidCredentialsError):
            log_in_workspace_spectator(
                session,
                workspace_slug=graph.slug,
                password=SPECTATOR_PASSWORD,
                session_lifetime_hours=8,
                browser_id="final-deletion-browser",
            )
        session.rollback()

    with SessionFactory() as session:
        assert session.scalar(select(User.id).where(User.id == graph.administrator_id)) is None


# Isolation from providers, decryption, email, and frontend.


def test_deletion_never_decrypts_or_calls_providers(
    graphs: Callable[..., DeletionGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.credential_vault import ProviderCredentialVault

    def forbidden(*_args, **_kwargs):
        raise AssertionError("final deletion must not decrypt credentials")

    monkeypatch.setattr(ProviderCredentialVault, "decrypt", forbidden)
    graph = graphs()
    enqueue_deletion_job(graph)

    assert run_worker(graph.scheduled_for).succeeded

    tree = ast.parse(inspect.getsource(workspace_deletion_execution))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    # Recipient encryption is the only notification boundary final deletion
    # may touch; it never imports a transport, renderer, or provider.
    allowed = {"app.notifications.recipient_encryption"}
    assert not any(
        name.startswith(("app.providers", "app.notifications", "app.api"))
        for name in imported - allowed
    )
    for module in (workspace_deletion_execution, workspace_deletion_email):
        source = inspect.getsource(module)
        for forbidden_name in ("email_provider", "resend_transport", "email_rendering", "httpx"):
            assert forbidden_name not in source
