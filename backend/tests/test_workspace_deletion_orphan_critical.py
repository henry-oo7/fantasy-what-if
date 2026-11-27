# ruff: noqa: F811  (imported pytest fixtures are used as test parameters)
"""Final workspace deletion decides orphan state after its own users cascade (ADR 0010).

After final deletion commits, every surviving canonical League either keeps
active personal access, keeps a valid workspace dependency (a matching
claim), or carries ``orphaned_at`` set to the deletion's timestamp. Real
PostgreSQL; concurrency through events and observed lock waits.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation, ProviderLeagueClaim
from app.db.session import SessionFactory, engine
from app.repositories.league_access_repository import LeagueAccessRepository
from app.repositories.provider_league_claim_repository import claimed_workspace_id
from app.repositories.workspace_deletion_repository import WorkspaceDeletionRepository
from app.services.workspace_deletion_execution import (
    WorkspaceDeletionOutcomeKind,
    execute_workspace_deletion,
)
from tests.support.league_import import (  # noqa: F401 - fixtures
    SEASON,
    FakeEspn,
    Registry,
    create_caller,
    espn,
    forbid_live_network,
    registry,
    run_import,
    unique_league_id,
)
from tests.support.workspace_deletion_graph import (
    DeletionGraph,
    cleanup_graphs,
    completion_deliveries,
    create_deletion_graph,
    deletion_recipient_cipher,
    deletion_records,
    enqueue_deletion_job,
    load_workspace,
)
from tests.test_league_import_critical import wait_for_lock_waiter
from tests.test_workspace_deletion_execution import run_worker

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SMALL = {"seasons": 1, "teams": 2, "weeks": 1, "players_per_team": 1}


@pytest.fixture
def graphs() -> Iterator[Callable[..., DeletionGraph]]:
    created: list[int] = []

    def create(**kwargs: Any) -> DeletionGraph:
        graph = create_deletion_graph(**{**SMALL, **kwargs})
        created.append(graph.workspace_id)
        return graph

    yield create
    cleanup_graphs(created)


@pytest.fixture
def independent_users() -> Iterator[Callable[[], int]]:
    """Users outside every workspace (future personal accounts)."""

    created: list[int] = []

    def create() -> int:
        with SessionFactory() as session:
            user = User(
                username=f"independent-{unique_league_id()}",
                display_name="Independent User",
                password_hash="$argon2id$synthetic",
                role=UserRole.SPECTATOR,
            )
            session.add(user)
            session.commit()
            created.append(user.id)
            return user.id

    yield create

    with SessionFactory() as session:
        session.execute(
            delete(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.user_id.in_(created))
        )
        session.execute(delete(User).where(User.id.in_(created)))
        session.commit()


def personal_league(registry: Registry) -> tuple[str, int, int]:
    """A canonical League with no workspace; returns (provider ID, League ID, Season ID)."""

    provider_league_id = unique_league_id()
    registry.league_ids.append(provider_league_id)

    with SessionFactory() as session:
        repository = LeagueAccessRepository(session)
        league = repository.lock_or_create_league(
            provider=FantasyProvider.ESPN, provider_league_id=provider_league_id, name="Personal"
        )
        season = repository.create_or_get_season(
            league_id=league.id, year=SEASON, current_week=1, regular_season_weeks=14
        )
        session.commit()
        return provider_league_id, league.id, season.id


def grant(user_id: int, season_id: int) -> None:
    with SessionFactory() as session:
        LeagueAccessRepository(session).create_or_get_access(
            user_id=user_id,
            season_id=season_id,
            proof_mode=UserSeasonProofMode.PUBLIC,
            provider_connection_id=None,
            verified_at=NOW,
        )
        session.commit()


def league_state(league_id: int) -> League | None:
    with SessionFactory() as session:
        league = session.get(League, league_id)
        if league is not None:
            session.expunge(league)
        return league


def season_exists(season_id: int) -> bool:
    with SessionFactory() as session:
        return session.get(Season, season_id) is not None


def active_access(user_id: int, season_id: int) -> bool:
    with SessionFactory() as session:
        return bool(
            session.scalar(
                select(func.count())
                .select_from(UserLeagueSeasonAccess)
                .where(
                    UserLeagueSeasonAccess.user_id == user_id,
                    UserLeagueSeasonAccess.season_id == season_id,
                    UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
                )
            )
        )


def user_exists(user_id: int) -> bool:
    with SessionFactory() as session:
        return session.get(User, user_id) is not None


def delete_due(graph: DeletionGraph) -> None:
    enqueue_deletion_job(graph)
    assert run_worker(graph.scheduled_for).succeeded


# --------------------------------------------------------------------------
# 1-4: final orphan state
# --------------------------------------------------------------------------


def test_admin_owning_the_only_access_leaves_league_orphaned(graphs, registry: Registry) -> None:
    graph = graphs()
    _, league_id, season_id = personal_league(registry)
    grant(graph.administrator_id, season_id)

    delete_due(graph)

    assert not user_exists(graph.administrator_id)
    assert not active_access(graph.administrator_id, season_id)
    league = league_state(league_id)
    assert league is not None and season_exists(season_id)
    assert league.orphaned_at == graph.scheduled_for  # the deletion's own timestamp
    assert len(completion_deliveries(graph.workspace_id)) == 1
    assert len(deletion_records(graph.workspace_id)) == 1


def test_independent_users_access_keeps_league_unorphaned(
    graphs, registry: Registry, independent_users
) -> None:
    graph = graphs()
    _, league_id, season_id = personal_league(registry)
    other = independent_users()
    grant(graph.administrator_id, season_id)
    grant(other, season_id)

    delete_due(graph)

    assert not user_exists(graph.administrator_id)
    assert not active_access(graph.administrator_id, season_id)
    assert user_exists(other) and active_access(other, season_id)
    league = league_state(league_id)
    assert league is not None and league.orphaned_at is None and season_exists(season_id)


def test_other_workspace_dependency_keeps_league_unorphaned(graphs) -> None:
    deleting = graphs()
    other = graphs(pending=False)
    # The deleting workspace's administrator holds the only personal access to
    # the other workspace's League.
    grant(deleting.administrator_id, other.season_ids[0])

    delete_due(deleting)

    league = league_state(other.league_id)
    assert league is not None and league.orphaned_at is None
    assert league.installation_id == other.workspace_id
    with SessionFactory() as session:
        assert claimed_workspace_id(session, league) == other.workspace_id
        assert session.get(ProviderLeagueClaim, other.claim_id) is not None
    assert not active_access(deleting.administrator_id, other.season_ids[0])
    assert season_exists(other.season_ids[0])


def test_league_without_personal_access_is_orphaned(graphs) -> None:
    graph = graphs()

    delete_due(graph)

    league = league_state(graph.league_id)
    assert league is not None and league.installation_id is None
    assert league.orphaned_at == graph.scheduled_for
    assert all(season_exists(season_id) for season_id in graph.season_ids)


# --------------------------------------------------------------------------
# 5: import racing final deletion
# --------------------------------------------------------------------------


def test_import_committed_first_prevents_orphaning(
    graphs, registry: Registry, espn: FakeEspn, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = graphs()
    provider_league_id, league_id, season_id = personal_league(registry)
    grant(graph.administrator_id, season_id)
    importer = create_caller(registry)
    original = LeagueAccessRepository.lock_or_create_league
    locked, release = threading.Event(), threading.Event()

    def pause_after_lock(self: LeagueAccessRepository, **kwargs: Any) -> League:
        league = original(self, **kwargs)
        locked.set()
        assert release.wait(10)
        return league

    monkeypatch.setattr(LeagueAccessRepository, "lock_or_create_league", pause_after_lock)
    enqueue_deletion_job(graph)
    outcome: dict[str, Any] = {}
    importing = threading.Thread(
        target=lambda: outcome.update(r=run_import(importer, provider_league_id, espn))
    )
    importing.start()
    assert locked.wait(10)
    deleting = threading.Thread(target=lambda: outcome.update(d=run_worker(graph.scheduled_for)))
    deleting.start()
    wait_for_lock_waiter()  # deletion waits on the import's League lock
    release.set()
    importing.join(15)
    deleting.join(15)

    assert outcome["d"].succeeded and outcome["r"].season_id == season_id
    league = league_state(league_id)
    assert league is not None and league.orphaned_at is None
    assert active_access(importer.user_id, season_id)
    assert not user_exists(graph.administrator_id)


def test_deletion_first_orphans_then_later_import_revives(
    graphs, registry: Registry, espn: FakeEspn, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = graphs()
    provider_league_id, league_id, season_id = personal_league(registry)
    grant(graph.administrator_id, season_id)
    importer = create_caller(registry)
    original_detach = WorkspaceDeletionRepository.detach_leagues
    original_mark = LeagueAccessRepository.mark_league_orphaned
    locked, release = threading.Event(), threading.Event()
    marks: list[tuple[int, bool]] = []

    def pause_after_detach(self: WorkspaceDeletionRepository, workspace_id: int) -> list[int]:
        league_ids = original_detach(self, workspace_id)
        locked.set()
        assert release.wait(10)
        return league_ids

    def record_mark(self: LeagueAccessRepository, **kwargs: Any) -> bool:
        marked = original_mark(self, **kwargs)
        marks.append((kwargs["league_id"], marked))
        return marked

    monkeypatch.setattr(WorkspaceDeletionRepository, "detach_leagues", pause_after_detach)
    monkeypatch.setattr(LeagueAccessRepository, "mark_league_orphaned", record_mark)
    enqueue_deletion_job(graph)
    outcome: dict[str, Any] = {}
    deleting = threading.Thread(target=lambda: outcome.update(d=run_worker(graph.scheduled_for)))
    deleting.start()
    assert locked.wait(10)
    importing = threading.Thread(
        target=lambda: outcome.update(r=run_import(importer, provider_league_id, espn))
    )
    importing.start()
    wait_for_lock_waiter()  # the import waits on the deletion's League lock
    release.set()
    deleting.join(15)
    importing.join(15)

    assert outcome["d"].succeeded and outcome["r"].season_id == season_id
    assert (league_id, True) in marks  # deletion committed the orphan mark first
    league = league_state(league_id)
    assert league is not None and league.orphaned_at is None  # revived by the import
    assert season_exists(season_id) and active_access(importer.user_id, season_id)


# --------------------------------------------------------------------------
# 6: failure during the final orphan calculation
# --------------------------------------------------------------------------


def test_failure_in_final_orphan_calculation_rolls_back_everything(
    graphs, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = graphs()
    _, league_id, season_id = personal_league(registry)
    grant(graph.administrator_id, season_id)

    def explode(self: WorkspaceDeletionRepository, league_ids: list[int], **kwargs: Any) -> None:
        raise OperationalError("injected orphan failure", {}, Exception())

    monkeypatch.setattr(WorkspaceDeletionRepository, "mark_orphaned_leagues", explode)
    enqueue_deletion_job(graph)

    result = run_worker(graph.scheduled_for)

    assert result.claimed and not result.succeeded
    workspace = load_workspace(graph.workspace_id)
    assert workspace is not None
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    assert user_exists(graph.administrator_id) and user_exists(graph.spectator_id)
    assert active_access(graph.administrator_id, season_id)
    own = league_state(graph.league_id)
    assert own is not None and own.installation_id == graph.workspace_id
    assert own.orphaned_at is None
    personal = league_state(league_id)
    assert personal is not None and personal.orphaned_at is None
    with SessionFactory() as session:
        assert session.get(ProviderLeagueClaim, graph.claim_id) is not None
        assert session.scalars(
            select(Season.provider_connection_id).where(Season.id.in_(graph.season_ids))
        ).all() == [graph.connection_id] * len(graph.season_ids)
    assert completion_deliveries(graph.workspace_id) == []
    assert deletion_records(graph.workspace_id) == []


# --------------------------------------------------------------------------
# 7: two final-deletion workers
# --------------------------------------------------------------------------


def test_two_deletion_workers_stay_idempotent(graphs, registry: Registry) -> None:
    graph = graphs()
    _, league_id, season_id = personal_league(registry)
    grant(graph.administrator_id, season_id)
    job_id = enqueue_deletion_job(graph)
    results: list[Any] = []
    barrier = threading.Barrier(2, timeout=10)

    def work() -> None:
        barrier.wait()
        results.append(run_worker(graph.scheduled_for))

    workers = [threading.Thread(target=work) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)

    assert sum(result.succeeded for result in results) == 1
    # A replayed attempt after completion is a no-op.
    with Session(engine) as session:
        assert (
            execute_workspace_deletion(
                session,
                workspace_id=graph.workspace_id,
                job_id=job_id,
                attempt_count=1,
                now=graph.scheduled_for,
                recipient_cipher_factory=deletion_recipient_cipher,
            )
            is WorkspaceDeletionOutcomeKind.ALREADY_COMPLETED
        )
        session.rollback()

    assert len(completion_deliveries(graph.workspace_id)) == 1
    assert len(deletion_records(graph.workspace_id)) == 1
    for pk in (league_id, graph.league_id):
        league = league_state(pk)
        assert league is not None and league.orphaned_at == graph.scheduled_for


# --------------------------------------------------------------------------
# 8: cross-workspace and cross-user rows
# --------------------------------------------------------------------------


def test_cross_workspace_and_cross_user_rows_stay_untouched(
    graphs, registry: Registry, independent_users
) -> None:
    deleting = graphs()
    other = graphs(pending=False)
    _, league_id, season_id = personal_league(registry)
    independent = independent_users()
    for user_id in (deleting.administrator_id, other.administrator_id, independent):
        grant(user_id, season_id)
    grant(independent, other.season_ids[0])

    def snapshot() -> tuple[Any, ...]:
        with SessionFactory() as session:
            workspace = session.get(Installation, other.workspace_id)
            league = session.get(League, other.league_id)
            claim = session.get(ProviderLeagueClaim, other.claim_id)
            assert workspace is not None and league is not None and claim is not None
            return (
                workspace.registration_status,
                workspace.updated_at,
                league.installation_id,
                league.orphaned_at,
                league.updated_at,
                claim.updated_at,
                session.scalars(
                    select(UserLeagueSeasonAccess.id)
                    .where(
                        UserLeagueSeasonAccess.user_id.in_((other.administrator_id, independent))
                    )
                    .order_by(UserLeagueSeasonAccess.id)
                ).all(),
                session.scalars(
                    select(User.id).where(User.workspace_id == other.workspace_id).order_by(User.id)
                ).all(),
            )

    before = snapshot()

    delete_due(deleting)

    assert snapshot() == before
    league = league_state(league_id)
    assert league is not None and league.orphaned_at is None
    assert active_access(other.administrator_id, season_id)
    assert active_access(independent, season_id)
    assert not user_exists(deleting.administrator_id)
