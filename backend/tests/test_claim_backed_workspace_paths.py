"""Every workspace League selection requires the workspace's own claim.

``League.installation_id`` is compatibility metadata (ADR 0010). Each path
below must succeed with installation_id plus a matching claim and be denied
for each broken claim, with no other workspace or claim owner disclosed.
Real PostgreSQL, synthetic committed data.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_current_user
from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.db.session import engine
from app.main import app
from app.repositories.league_access_repository import LeagueAccessRepository
from app.repositories.league_repository import list_imported_seasons
from app.services.background_job_handlers import validate_sync_job_ownership
from app.services.background_job_worker import BackgroundJobExecutionError
from app.services.provider_sync import prepare_season_sync
from app.services.scheduled_synchronization_jobs import list_scheduled_synchronization_targets
from app.services.season_import import SeasonImportUnavailableError, _single_league
from app.services.season_sync_jobs import enqueue_season_sync
from tests.support.provider_sync_fixtures import (
    SyntheticSeasonAdapter,
    encrypt_synthetic_credentials,
    forbid_live_network,
    registry_for,
    worker_settings,
)
from tests.support.workspace_deletion_graph import (
    cleanup_graphs,
    create_deletion_graph,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


# --------------------------------------------------------------------------
# Claim mutations applied to a ready workspace (installation_id stays set).
# --------------------------------------------------------------------------


class Env:
    def __init__(self) -> None:
        self.graph = create_deletion_graph(seasons=1, pending=False)
        self.workspace_id = self.graph.workspace_id
        self.season_id = self.graph.season_ids[0]
        self.extra_installations: list[int] = []
        self.user_ids: list[int] = []

        # Same re-encryption graph_factory does, so the workspace credential decrypts.
        with Session(engine) as session:
            connection = session.get(ProviderConnection, self.graph.connection_id)
            connection.encrypted_credentials = encrypt_synthetic_credentials(
                self.graph.provider_league_id, swid="{SYNTH-SWID}", espn_s2="SYNTH-S2"
            )
            connection.credential_version = 1
            session.commit()

    def mutate(self, name: str) -> None:
        graph = self.graph
        with Session(engine) as session:
            claim = session.get(ProviderLeagueClaim, graph.claim_id)
            if name == "missing-claim":
                session.delete(claim)
            elif name == "provider-mismatch":
                claim.provider = FantasyProvider.SLEEPER
            elif name == "normalized-id-mismatch":
                other_identity = "9" + graph.provider_league_id
                claim.provider_league_id = other_identity
                claim.normalized_provider_league_id = other_identity
            elif name == "other-workspace-claim":
                other = Installation(
                    application_name="Other Workspace",
                    timezone="America/Chicago",
                    status=InstallationStatus.ACTIVE,
                )
                session.add(other)
                session.flush()
                self.extra_installations.append(other.id)
                claim.workspace_id = other.id
            elif name == "personal-access-only":
                session.delete(claim)
                user = User(
                    username=f"personal-{graph.workspace_id}",
                    display_name="Personal User",
                    password_hash="$argon2id$synthetic",
                    role=UserRole.SPECTATOR,
                )
                session.add(user)
                session.flush()
                self.user_ids.append(user.id)
                session.flush()
                LeagueAccessRepository(session).create_or_get_access(
                    user_id=user.id,
                    season_id=self.season_id,
                    proof_mode=UserSeasonProofMode.PUBLIC,
                    provider_connection_id=None,
                    verified_at=NOW,
                )
            else:
                raise AssertionError(name)
            session.commit()

    def cleanup(self) -> None:
        with Session(engine) as session:
            session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.idempotency_key == f"league-season-sync:{self.season_id}"
                )
            )
            session.execute(
                delete(UserLeagueSeasonAccess).where(
                    UserLeagueSeasonAccess.user_id.in_(self.user_ids)
                )
            )
            session.execute(delete(User).where(User.id.in_(self.user_ids)))
            session.commit()
        cleanup_graphs([self.workspace_id])
        with Session(engine) as session:
            session.execute(
                delete(Installation).where(Installation.id.in_(self.extra_installations))
            )
            session.commit()


@pytest.fixture
def env() -> Iterator[Callable[[], Env]]:
    created: list[Env] = []

    def make() -> Env:
        created.append(Env())
        return created[-1]

    yield make
    for item in created:
        item.cleanup()


# --------------------------------------------------------------------------
# One predicate per production path: True when the workspace is authorized.
# --------------------------------------------------------------------------


def imported_seasons(env: Env) -> bool:
    with Session(engine) as session:
        return bool(
            list_imported_seasons(session, installation_id=env.workspace_id, active_season_id=None)
        )


def single_league(env: Env) -> bool:
    with Session(engine) as session:
        try:
            _single_league(session, workspace_id=env.workspace_id, lock=False)
        except SeasonImportUnavailableError:
            return False
        return True


def scheduled_target(env: Env) -> bool:
    with Session(engine) as session:
        return any(
            target.workspace_id == env.workspace_id
            for target in list_scheduled_synchronization_targets(session)
        )


def season_job_workspace(env: Env) -> bool:
    """The Season job carries the workspace id only while the claim backs it."""

    with Session(engine) as session:
        result = enqueue_season_sync(session, season_id=env.season_id)
        session.commit()
        return result.job.workspace_id == env.workspace_id


def job_ownership(env: Env) -> bool:
    with Session(engine) as session:
        try:
            validate_sync_job_ownership(
                session, workspace_id=env.workspace_id, season_id=env.season_id
            )
        except BackgroundJobExecutionError as exc:
            assert exc.safe_error_code == "workspace_ownership_mismatch"
            return False
        return True


def sync_fallback(env: Env) -> bool:
    """The workspace credential fallback is selected only with a backing claim."""

    adapter = SyntheticSeasonAdapter(
        provider_league_id=env.graph.provider_league_id, dataset="alpha"
    )
    try:
        plan, _ = prepare_season_sync(
            session_factory=lambda: Session(engine),
            season_id=env.season_id,
            settings=worker_settings(),
            registry=registry_for(adapter),
        )
    except BackgroundJobExecutionError as exc:
        assert exc.safe_error_code == "provider_reverification_required"
        return False
    return plan.legacy_workspace_id == env.workspace_id


PATHS = {
    "imported-seasons": imported_seasons,
    "season-import-league": single_league,
    "scheduled-targets": scheduled_target,
    "season-job-workspace": season_job_workspace,
    "job-ownership": job_ownership,
    "sync-fallback": sync_fallback,
}
MUTATIONS = [
    "missing-claim",
    "provider-mismatch",
    "normalized-id-mismatch",
    "other-workspace-claim",
    "personal-access-only",
]


@pytest.mark.parametrize("path", PATHS)
def test_installation_id_with_matching_claim_is_authorized(env, path: str) -> None:
    assert PATHS[path](env()) is True


@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize("path", PATHS)
def test_installation_id_without_a_matching_claim_is_denied(env, path: str, mutation: str) -> None:
    ready = env()
    ready.mutate(mutation)

    with Session(engine) as session:
        league = session.scalar(select(League).where(League.id == ready.graph.league_id))
        assert league.installation_id == ready.workspace_id  # metadata alone is present

    assert PATHS[path](ready) is False


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_denied_api_response_discloses_no_other_workspace_or_claim_owner(
    env, mutation: str
) -> None:
    ready = env()
    ready.mutate(mutation)
    with Session(engine) as session:
        administrator = session.get(User, ready.graph.administrator_id)
        session.expunge(administrator)

    app.dependency_overrides[get_current_user] = lambda: administrator
    try:
        response = TestClient(app).get("/api/leagues/seasons")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 503
    body = response.text
    for extra in ready.extra_installations:
        assert str(extra) not in body
    assert ready.graph.provider_league_id not in body
    assert "Other Workspace" not in body
