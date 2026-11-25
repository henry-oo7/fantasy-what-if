"""Critical invariants of the workspace/canonical League compatibility boundary.

Real PostgreSQL, committed data, lock observation instead of sleeps.
Workspace deletion must never destroy shared canonical data (ADR 0010).
"""

import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season, Team
from app.db.platform_enums import FantasyProvider, UserSeasonProofMode
from app.db.platform_models import Installation, ProviderConnection, ProviderLeagueClaim
from app.db.session import engine
from app.repositories.league_access_repository import LeagueAccessRepository
from tests.support.workspace_deletion_graph import (
    DeletionGraph,
    cleanup_graphs,
    create_deletion_graph,
    enqueue_deletion_job,
    shared_row_counts,
)
from tests.test_workspace_deletion_execution import run_worker

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def graphs() -> Iterator[Callable[..., DeletionGraph]]:
    created: list[int] = []
    users: list[int] = []

    def create(*, personal_access: bool = False, **kwargs) -> DeletionGraph:
        graph = create_deletion_graph(**kwargs)
        created.append(graph.workspace_id)
        if personal_access:
            with Session(engine) as session:
                user = User(
                    username=f"personal-{graph.workspace_id}",
                    display_name="Personal User",
                    password_hash="$argon2id$synthetic",
                    role=UserRole.SPECTATOR,
                )
                session.add(user)
                session.flush()
                users.append(user.id)
                connection = ProviderConnection(
                    user_id=user.id, provider=FantasyProvider.ESPN, display_name="Personal"
                )
                session.add(connection)
                session.flush()
                LeagueAccessRepository(session).create_or_get_access(
                    user_id=user.id,
                    season_id=graph.season_ids[0],
                    proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
                    provider_connection_id=connection.id,
                    verified_at=NOW,
                )
                session.commit()
        return graph

    yield create

    with Session(engine) as session:
        session.execute(
            delete(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.user_id.in_(users))
        )
        session.execute(delete(ProviderConnection).where(ProviderConnection.user_id.in_(users)))
        session.execute(delete(User).where(User.id.in_(users)))
        session.commit()
    cleanup_graphs(created)


def league_state(league_id: int) -> League | None:
    with Session(engine) as session:
        league = session.get(League, league_id)
        if league is not None:
            session.expunge(league)
        return league


def delete_workspace(graph: DeletionGraph) -> None:
    before = shared_row_counts(graph)
    enqueue_deletion_job(graph)
    assert run_worker(graph.scheduled_for).succeeded
    assert shared_row_counts(graph) == before  # no shared canonical row was lost


def test_deletion_preserves_shared_league_and_personal_access(graphs) -> None:
    graph = graphs(personal_access=True)
    with Session(engine) as session:
        teams_before = session.scalar(
            select(func.count()).select_from(Team).where(Team.season_id.in_(graph.season_ids))
        )
        personal_connection = session.scalar(
            select(ProviderConnection.id).where(
                ProviderConnection.user_id.is_not(None),
                ProviderConnection.display_name == "Personal",
            )
        )

    delete_workspace(graph)

    league = league_state(graph.league_id)
    assert league is not None and league.installation_id is None and league.orphaned_at is None
    with Session(engine) as session:
        assert session.get(Installation, graph.workspace_id) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProviderLeagueClaim)
                .where(ProviderLeagueClaim.id == graph.claim_id)
            )
            == 0
        )
        assert session.scalar(
            select(func.count()).select_from(Season).where(Season.id.in_(graph.season_ids))
        ) == len(graph.season_ids)
        assert (
            session.scalar(
                select(func.count()).select_from(Team).where(Team.season_id.in_(graph.season_ids))
            )
            == teams_before
        )
        assert session.scalars(
            select(Season.provider_connection_id).where(Season.id.in_(graph.season_ids))
        ).all() == [None] * len(graph.season_ids)
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserLeagueSeasonAccess)
                .where(
                    UserLeagueSeasonAccess.season_id == graph.season_ids[0],
                    UserLeagueSeasonAccess.provider_connection_id == personal_connection,
                )
            )
            == 1
        )
        assert session.get(ProviderConnection, graph.connection_id) is None


def test_deletion_without_personal_access_orphans_instead_of_deleting(graphs) -> None:
    graph = graphs()

    delete_workspace(graph)

    league = league_state(graph.league_id)
    assert league is not None
    assert league.installation_id is None
    assert league.orphaned_at is not None
    with Session(engine) as session:
        assert session.scalar(
            select(func.count()).select_from(Season).where(Season.id.in_(graph.season_ids))
        ) == len(graph.season_ids)


def test_canonical_get_or_create_is_race_safe_and_never_duplicates() -> None:
    identity = f"7{time.time_ns()}"
    barrier = threading.Barrier(2)
    ids: list[int] = []

    def create() -> None:
        with Session(engine) as session:
            barrier.wait()
            league = LeagueAccessRepository(session).lock_or_create_league(
                provider=FantasyProvider.ESPN, provider_league_id=identity, name="Race League"
            )
            ids.append(league.id)
            session.commit()

    threads = [threading.Thread(target=create) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    try:
        assert len(ids) == 2 and ids[0] == ids[1]
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(League)
                    .where(League.provider_league_id == identity)
                )
                == 1
            )
    finally:
        with Session(engine) as session:
            session.execute(delete(League).where(League.provider_league_id == identity))
            session.commit()


def test_personal_access_committed_first_prevents_orphaning_race(graphs) -> None:
    """Access creation holds the League lock; deletion waits, then sees active access."""

    graph = graphs()
    user_ids: list[int] = []
    holder = Session(engine)
    try:
        user = User(
            username=f"racer-{graph.workspace_id}",
            display_name="Racer",
            password_hash="$argon2id$synthetic",
            role=UserRole.SPECTATOR,
        )
        holder.add(user)
        holder.flush()
        user_ids.append(user.id)
        LeagueAccessRepository(holder).create_or_get_access(
            user_id=user.id,
            season_id=graph.season_ids[0],
            proof_mode=UserSeasonProofMode.PUBLIC,
            provider_connection_id=None,
            verified_at=NOW,
        )  # League row lock held, uncommitted.

        enqueue_deletion_job(graph)
        result: list[object] = []
        worker = threading.Thread(target=lambda: result.append(run_worker(graph.scheduled_for)))
        worker.start()

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with engine.connect() as probe:
                waiting = probe.scalar(
                    text(
                        "select count(*) from pg_stat_activity where wait_event_type = 'Lock' "
                        "and datname = current_database()"
                    )
                )
            if waiting:
                break
            time.sleep(0.02)
        assert waiting, "deletion did not block on the League lock"

        holder.commit()
        worker.join(timeout=15)
        assert result and result[0].succeeded

        league = league_state(graph.league_id)
        assert league is not None and league.orphaned_at is None and league.installation_id is None
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(UserLeagueSeasonAccess)
                    .where(UserLeagueSeasonAccess.user_id.in_(user_ids))
                )
                == 1
            )
    finally:
        holder.close()
        with Session(engine) as session:
            session.execute(
                delete(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.user_id.in_(user_ids))
            )
            session.execute(delete(User).where(User.id.in_(user_ids)))
            session.commit()
