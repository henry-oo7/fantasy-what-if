"""Operator re-enqueue tooling for FR-20 final workspace deletion."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob
from app.db.session import SessionFactory
from app.repositories.background_job_repository import BackgroundJobRepository
from app.scripts.reenqueue_workspace_deletion import parse_arguments, run_reenqueue
from app.services.background_job_worker import snapshot_background_job
from app.services.workspace_deletion_execution import WorkspaceDeletionJobHandler
from tests.support.workspace_deletion_graph import (
    DeletionGraph,
    cleanup_graphs,
    create_deletion_graph,
    enqueue_deletion_job,
    load_workspace,
    owned_row_counts,
)


@pytest.fixture
def graphs() -> Iterator[Callable[..., DeletionGraph]]:
    created: list[int] = []

    def create(**kwargs) -> DeletionGraph:
        graph = create_deletion_graph(**kwargs)
        created.append(graph.workspace_id)
        return graph

    yield create

    cleanup_graphs(created)


def deletion_jobs(workspace_id: int) -> list[BackgroundJob]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(BackgroundJob)
                .where(
                    BackgroundJob.workspace_id == workspace_id,
                    BackgroundJob.job_type == BackgroundJobType.WORKSPACE_DELETION,
                )
                .order_by(BackgroundJob.id)
            ).all()
        )
        for row in rows:
            session.expunge(row)

        return rows


def assert_no_secrets(output: str, graph: DeletionGraph) -> None:
    for secret in (
        graph.slug,
        graph.administrator_email,
        graph.ciphertext,
        graph.provider_league_id,
        graph.display_name,
    ):
        assert secret not in output


def test_workspace_id_argument_is_required() -> None:
    with pytest.raises(SystemExit):
        parse_arguments([])

    assert parse_arguments(["--workspace-id", "7"]).workspace_id == 7


def test_creates_one_job_without_changing_data(
    graphs: Callable[..., DeletionGraph],
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = graphs()
    before = owned_row_counts(graph)
    now = graph.scheduled_for + timedelta(days=1)

    exit_code = run_reenqueue(
        workspace_id=graph.workspace_id, session_factory=SessionFactory, now=now
    )

    output = capsys.readouterr().out
    [job] = deletion_jobs(graph.workspace_id)
    assert exit_code == 0
    assert output.strip() == (
        f"OK: workspace_id={graph.workspace_id} job_id={job.id} job_status=pending job=created"
    )
    assert job.payload == {}
    assert job.available_at == now
    assert job.idempotency_key == f"workspace_deletion:{graph.workspace_id}"
    after = owned_row_counts(graph)
    assert {k: v for k, v in after.items() if k != "background_jobs"} == {
        k: v for k, v in before.items() if k != "background_jobs"
    }
    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    assert_no_secrets(output, graph)


def test_returns_existing_active_job(
    graphs: Callable[..., DeletionGraph],
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = graphs()
    job_id = enqueue_deletion_job(graph)

    exit_code = run_reenqueue(
        workspace_id=graph.workspace_id,
        session_factory=SessionFactory,
        now=graph.scheduled_for,
    )

    assert exit_code == 0
    assert f"job_id={job_id} job_status=pending job=existing" in capsys.readouterr().out
    assert [job.id for job in deletion_jobs(graph.workspace_id)] == [job_id]


def test_creates_new_job_after_terminal_failure(
    graphs: Callable[..., DeletionGraph],
) -> None:
    graph = graphs()
    failed_id = enqueue_deletion_job(graph)
    with SessionFactory() as session:
        job = session.get(BackgroundJob, failed_id)
        job.status = BackgroundJobStatus.FAILED
        job.failed_at = graph.scheduled_for
        session.commit()

    assert (
        run_reenqueue(
            workspace_id=graph.workspace_id,
            session_factory=SessionFactory,
            now=graph.scheduled_for + timedelta(hours=1),
        )
        == 0
    )

    jobs = deletion_jobs(graph.workspace_id)
    assert [job.status for job in jobs] == [BackgroundJobStatus.FAILED, BackgroundJobStatus.PENDING]


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("not_found", "workspace_not_found"),
        ("not_pending", "workspace_not_deletion_pending"),
        ("not_due", "deletion_not_due"),
        ("completed", "deletion_already_completed"),
        ("non_positive", None),
    ],
)
def test_refusals_change_nothing(
    graphs: Callable[..., DeletionGraph],
    capsys: pytest.CaptureFixture[str],
    case: str,
    reason: str | None,
) -> None:
    graph = graphs(pending=case != "not_pending")
    workspace_id = graph.workspace_id
    now = graph.scheduled_for + timedelta(days=1)

    if case == "not_found":
        workspace_id = 2_000_000_000
    elif case == "not_due":
        now = graph.scheduled_for - timedelta(microseconds=1)
    elif case == "non_positive":
        workspace_id = 0
    elif case == "completed":
        job_id = enqueue_deletion_job(graph)
        with SessionFactory() as session:
            job = BackgroundJobRepository(session).claim_next_available(
                worker_id="reenqueue-test", lease_duration=timedelta(minutes=5), now=now
            )
            assert job is not None and job.id == job_id
            snapshot = snapshot_background_job(job)
            session.commit()
        WorkspaceDeletionJobHandler(session_factory=SessionFactory, now=lambda: now)(snapshot)

    before = owned_row_counts(graph)
    exit_code = run_reenqueue(workspace_id=workspace_id, session_factory=SessionFactory, now=now)

    output = capsys.readouterr().out
    assert exit_code == 2
    assert output.startswith("REFUSED:")
    if reason is not None:
        assert output.strip() == f"REFUSED: workspace_id={workspace_id} reason={reason}"
    assert owned_row_counts(graph) == before
    assert_no_secrets(output, graph)
