"""Large-fixture evidence that ordered single-transaction deletion is practical.

The fixture approximates a large league with a long history: 8 seasons,
14 teams, 18 weeks, and 20 rostered players per team-week (about 40k
``player_week_scores`` rows). The threshold is deliberately generous so
the check is not a flaky wall-clock benchmark; the measured duration is
printed (run with ``-s``) and attached as a test property.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import timedelta

import pytest

from app.db.session import SessionFactory
from app.repositories.background_job_repository import BackgroundJobRepository
from app.services.background_job_worker import snapshot_background_job
from app.services.workspace_deletion_execution import WorkspaceDeletionJobHandler
from tests.support.workspace_deletion_graph import (
    DeletionGraph,
    cleanup_graphs,
    create_deletion_graph,
    deletion_recipient_cipher,
    deletion_records,
    enqueue_deletion_job,
    owned_row_counts,
    shared_row_counts,
)

SEASONS = 8
TEAMS = 14
WEEKS = 18
PLAYERS_PER_TEAM = 20
GENEROUS_THRESHOLD_SECONDS = 60.0


@pytest.fixture
def large_graph() -> Iterator[DeletionGraph]:
    graph = create_deletion_graph(
        seasons=SEASONS,
        teams=TEAMS,
        weeks=WEEKS,
        players_per_team=PLAYERS_PER_TEAM,
    )
    yield graph
    cleanup_graphs([graph.workspace_id])


def test_large_workspace_deletes_in_one_practical_transaction(
    large_graph: DeletionGraph,
    record_property,
) -> None:
    job_id = enqueue_deletion_job(large_graph)
    owned = owned_row_counts(large_graph)
    before = shared_row_counts(large_graph)
    assert before["player_week_scores"] == SEASONS * TEAMS * WEEKS * PLAYERS_PER_TEAM

    now = large_graph.scheduled_for + timedelta(seconds=1)
    with SessionFactory() as session:
        job = BackgroundJobRepository(session).claim_next_available(
            worker_id="large-fixture-worker", lease_duration=timedelta(minutes=5), now=now
        )
        assert job is not None and job.id == job_id
        snapshot = snapshot_background_job(job)
        session.commit()

    handler = WorkspaceDeletionJobHandler(
        session_factory=SessionFactory,
        recipient_cipher_factory=deletion_recipient_cipher,
        now=lambda: now,
    )
    started = time.perf_counter()
    handler(snapshot)
    duration = time.perf_counter() - started

    total_rows = sum(owned.values()) + sum(before.values())
    record_property("deletion_seconds", round(duration, 3))
    record_property("fixture_rows", total_rows)
    print(
        f"\nLARGE FIXTURE DELETION: rows={total_rows} "
        f"player_week_scores={before['player_week_scores']} "
        f"matchups={before['matchups']} "
        f"optimal_lineup_results={before['optimal_lineup_results']} "
        f"teams={before['teams']} seasons={before['seasons']} "
        f"duration_seconds={duration:.3f}"
    )

    assert all(count == 0 for count in owned_row_counts(large_graph).values())
    assert shared_row_counts(large_graph) == before  # shared canonical data survives
    assert len(deletion_records(large_graph.workspace_id)) == 1
    assert duration < GENEROUS_THRESHOLD_SECONDS
