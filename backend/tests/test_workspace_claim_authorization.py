"""Workspace authorization requires the workspace's own ProviderLeagueClaim.

``League.installation_id`` is compatibility metadata only (ADR 0010); a
matching value without a matching claim must not authorize. Real PostgreSQL,
synthetic committed data.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_current_user
from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League
from app.db.platform_enums import FantasyProvider, UserSeasonProofMode
from app.db.platform_models import Installation, ProviderConnection, ProviderLeagueClaim
from app.db.session import engine
from app.main import app
from app.repositories.league_access_repository import LeagueAccessRepository
from app.services.active_season_context import (
    ActiveSeasonResolutionError,
    ViewSeasonResolutionError,
    resolve_active_season_context,
    resolve_view_season_context,
)
from tests.support.workspace_deletion_graph import (
    DeletionGraph,
    cleanup_graphs,
    create_deletion_graph,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def graphs() -> Iterator[Callable[[], DeletionGraph]]:
    created: list[int] = []
    user_ids: list[int] = []

    def create() -> DeletionGraph:
        graph = create_deletion_graph(pending=False)
        created.append(graph.workspace_id)
        return graph

    create.user_ids = user_ids  # type: ignore[attr-defined]
    yield create

    with Session(engine) as session:
        session.execute(
            delete(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.user_id.in_(user_ids))
        )
        session.execute(delete(ProviderConnection).where(ProviderConnection.user_id.in_(user_ids)))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.commit()
    cleanup_graphs(created)


def resolve_all(graph: DeletionGraph) -> list[str]:
    """Run every workspace authorization path; return which ones were denied."""

    denied: list[str] = []
    attempts = {
        "active": lambda s: resolve_active_season_context(
            session=s, workspace_id=graph.workspace_id
        ),
        "view-requested": lambda s: resolve_view_season_context(
            session=s, workspace_id=graph.workspace_id, requested_season_id=graph.season_ids[0]
        ),
        "view-active": lambda s: resolve_view_season_context(
            session=s, workspace_id=graph.workspace_id
        ),
    }
    for name, attempt in attempts.items():
        with Session(engine) as session:
            try:
                attempt(session)
            except ActiveSeasonResolutionError, ViewSeasonResolutionError:
                denied.append(name)

    # Newest-imported-season fallback: no active season on the workspace.
    with Session(engine) as session:
        session.execute(
            update(Installation)
            .where(Installation.id == graph.workspace_id)
            .values(active_season_id=None)
        )
        try:
            resolve_view_season_context(session=session, workspace_id=graph.workspace_id)
        except ViewSeasonResolutionError:
            denied.append("view-newest")
        session.rollback()

    return denied


def test_correct_claim_with_compatibility_metadata_authorizes(graphs) -> None:
    assert resolve_all(graphs()) == []


def test_matching_installation_id_without_claim_is_denied(graphs) -> None:
    graph = graphs()
    with Session(engine) as session:
        session.execute(delete(ProviderLeagueClaim).where(ProviderLeagueClaim.id == graph.claim_id))
        session.commit()

    assert resolve_all(graph) == ["active", "view-requested", "view-active", "view-newest"]


def test_claim_for_a_different_provider_league_is_denied(graphs) -> None:
    graph = graphs()
    with Session(engine) as session:
        session.execute(
            update(ProviderLeagueClaim)
            .where(ProviderLeagueClaim.id == graph.claim_id)
            .values(
                provider_league_id="9" + graph.provider_league_id,
                normalized_provider_league_id="9" + graph.provider_league_id,
            )
        )
        session.commit()

    assert resolve_all(graph) == ["active", "view-requested", "view-active", "view-newest"]


def test_claim_for_a_different_provider_is_denied(graphs) -> None:
    graph = graphs()
    with Session(engine) as session:
        session.execute(
            update(ProviderLeagueClaim)
            .where(ProviderLeagueClaim.id == graph.claim_id)
            .values(provider=FantasyProvider.SLEEPER)
        )
        session.commit()

    assert resolve_all(graph) == ["active", "view-requested", "view-active", "view-newest"]


def test_another_workspaces_claim_is_denied(graphs) -> None:
    first, second = graphs(), graphs()
    with Session(engine) as session:
        # The second workspace's claim is moved onto the first workspace's identity.
        session.execute(delete(ProviderLeagueClaim).where(ProviderLeagueClaim.id == first.claim_id))
        session.execute(
            update(ProviderLeagueClaim)
            .where(ProviderLeagueClaim.id == second.claim_id)
            .values(
                provider_league_id=first.provider_league_id,
                normalized_provider_league_id=first.provider_league_id,
            )
        )
        session.commit()

    # First keeps installation_id but not the claim; second has the claim identity
    # but not installation_id. Neither is authorized for the first League.
    assert resolve_all(first) == ["active", "view-requested", "view-active", "view-newest"]
    with Session(engine) as session, pytest.raises(ViewSeasonResolutionError):
        resolve_view_season_context(
            session=session,
            workspace_id=second.workspace_id,
            requested_season_id=first.season_ids[0],
        )


def test_personal_access_alone_does_not_authorize_a_workspace(graphs) -> None:
    graph = graphs()
    with Session(engine) as session:
        user = User(
            username=f"personal-{graph.workspace_id}",
            display_name="Personal User",
            password_hash="$argon2id$synthetic",
            role=UserRole.SPECTATOR,
        )
        session.add(user)
        session.flush()
        graphs.user_ids.append(user.id)  # type: ignore[attr-defined]
        LeagueAccessRepository(session).create_or_get_access(
            user_id=user.id,
            season_id=graph.season_ids[0],
            proof_mode=UserSeasonProofMode.PUBLIC,
            provider_connection_id=None,
            verified_at=NOW,
        )
        # The workspace holds neither metadata nor claim for this League.
        session.execute(delete(ProviderLeagueClaim).where(ProviderLeagueClaim.id == graph.claim_id))
        session.execute(
            update(League).where(League.id == graph.league_id).values(installation_id=None)
        )
        session.commit()

    assert resolve_all(graph) == ["active", "view-requested", "view-active", "view-newest"]


def test_api_status_and_body_do_not_disclose_claim_details(graphs) -> None:
    graph = graphs()
    with Session(engine) as session:
        administrator = session.get(User, graph.administrator_id)
        session.expunge(administrator)
    app.dependency_overrides[get_current_user] = lambda: administrator
    try:
        client = TestClient(app)
        allowed = client.get("/api/analytics/all-play")
        with Session(engine) as session:
            session.execute(
                delete(ProviderLeagueClaim).where(ProviderLeagueClaim.id == graph.claim_id)
            )
            session.commit()
        denied = client.get("/api/analytics/all-play")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert allowed.status_code == 200
    assert denied.status_code == 503
    detail = denied.json()["detail"]
    assert str(graph.workspace_id) not in detail and graph.provider_league_id not in detail
    assert FantasyProvider.ESPN.value not in detail.lower()
