"""Onboarding initial synchronization: start/progress API and worker handler.

ESPN is never contacted: the worker uses a synthetic adapter registry and
a guard fails any outbound request. Credentials are synthetic.
"""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text

from app.api.auth_dependencies import get_current_user
from app.auth.cookies import csrf_cookie_name
from app.core.settings import get_settings
from app.db.auth_models import User, UserRole
from app.db.models import League, Matchup, Season, Team
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    FantasySport,
    InstallationStatus,
    OnboardingStage,
    ProviderAccessMode,
    SeasonStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.db.session import SessionFactory, engine
from app.main import app
from app.providers.credential_vault import ProviderCredentialVault
from app.providers.domain import (
    ProviderCapabilities,
    ProviderCredentials,
    ProviderLeague,
    ProviderMatchup,
    ProviderScheduleRules,
    ProviderTeam,
    ProviderValidationResult,
)
from app.providers.errors import (
    ProviderAuthenticationError,
    ProviderErrorContext,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.providers.registry import ProviderRegistry
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.session_repository import SessionRepository
from app.services.background_job_lease_recovery import recover_expired_background_jobs_committed
from app.services.background_job_worker import execute_one_background_job
from app.services.onboarding_initial_sync import (
    InitialWorkspaceSyncHandler,
    initial_sync_idempotency_key,
)

TRUSTED_ORIGIN = "http://localhost:5173"
ENDPOINT = "/api/onboarding/synchronization"
SEASON = 2024
KEY = Fernet.generate_key().decode()
SYNTHETIC_SWID = "{SYNTHETIC-SWID-SYNC-TEST}"
SYNTHETIC_S2 = "synthetic-espn-s2-sync-test-not-real"


# --------------------------------------------------------------------------
# Synthetic provider
# --------------------------------------------------------------------------


@dataclass
class FakeEspn:
    """Records adapter construction and fetches; fails on demand."""

    league_name: str = "Synthetic Saturday League"
    current_week: int = 1
    regular_season_weeks: int = 2
    fail_fetch_league: Exception | None = None
    fail_matchups: Exception | None = None
    returned_league_id: str | None = None
    built_with: list[dict[str, str]] = field(default_factory=list)
    open_connections_at_first_fetch: list[int] = field(default_factory=list)

    def registry(self) -> ProviderRegistry:
        registry = ProviderRegistry()
        registry.register(FantasyProvider.ESPN, self._factory)

        return registry

    def _factory(self, credentials: ProviderCredentials) -> FakeAdapter:
        values = dict(credentials.values)
        self.built_with.append(values)

        return FakeAdapter(self, values["league_id"], values["season"])


class FakeAdapter:
    provider = FantasyProvider.ESPN

    def __init__(self, fake: FakeEspn, league_id: str, season: str) -> None:
        self.fake = fake
        self.league_id = league_id
        self.season = season
        self.fetches = 0

    def validate_configuration(self) -> ProviderValidationResult:
        return ProviderValidationResult(is_valid=True, provider=self.provider, safe_message="ok")

    def discover_leagues(self) -> tuple:
        return ()

    def fetch_league(self, *, provider_league_id: str, provider_season_id: str) -> ProviderLeague:
        if self.fetches == 0:
            self.fake.open_connections_at_first_fetch.append(engine.pool.checkedout())

        self.fetches += 1

        if self.fake.fail_fetch_league is not None:
            raise self.fake.fail_fetch_league

        assert provider_league_id == self.league_id
        assert provider_season_id == self.season
        league_id = self.fake.returned_league_id or self.league_id

        return ProviderLeague(
            provider_league_id=league_id,
            provider_season_id=self.season,
            name=self.fake.league_name,
            sport=FantasySport.FOOTBALL,
            season_year=int(self.season),
            current_week=self.fake.current_week,
            regular_season_weeks=self.fake.regular_season_weeks,
            playoff_week_start=None,
            playoff_week_end=None,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=tuple(self._team(index) for index in (1, 2)),
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=ProviderScheduleRules(
                regular_season_weeks=self.fake.regular_season_weeks,
                playoff_week_start=None,
                playoff_week_end=None,
                has_divisions=False,
                has_median_game=False,
                has_doubleheaders=False,
                allows_ties=True,
            ),
            capabilities=ProviderCapabilities(
                supports_head_to_head=True,
                supports_weekly_matchups=True,
                supports_lineup_history=False,
                supports_optimal_lineups=False,
                supports_schedule_swap=True,
                supports_all_play=True,
                supports_league_average=True,
                supports_team_logos=False,
            ),
        )

    def fetch_matchups(
        self, *, provider_league_id: str, provider_season_id: str, weeks: tuple[int, ...]
    ) -> tuple[ProviderMatchup, ...]:
        if self.fake.fail_matchups is not None:
            raise self.fake.fail_matchups

        return tuple(
            ProviderMatchup(
                week=week,
                home_provider_team_id="team-1",
                away_provider_team_id="team-2",
                home_score=None,
                away_score=None,
                is_complete=False,
                is_bye=False,
            )
            for week in weeks
        )

    def fetch_weekly_rosters(
        self, *, provider_league_id: str, provider_season_id: str, weeks: tuple[int, ...]
    ) -> tuple:
        assert weeks == ()

        return ()

    @staticmethod
    def _team(index: int) -> ProviderTeam:
        return ProviderTeam(
            provider_team_id=f"team-{index}",
            name=f"Synthetic Team {index}",
            abbreviation=f"S{index}",
            owner_display_name=f"Synthetic Manager {index}",
            wins=0,
            losses=0,
            ties=0,
            points_for=Decimal("0.00"),
            points_against=Decimal("0.00"),
        )


def provider_error(error_type: type, *, retryable: bool, message: str) -> Exception:
    return error_type(
        context=ProviderErrorContext(
            provider=FantasyProvider.ESPN, operation="fetch_league", retryable=retryable
        ),
        safe_message=message,
    )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Live network requests are forbidden in tests.")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


@pytest.fixture(autouse=True)
def real_authentication() -> Iterator[None]:
    inherited = app.dependency_overrides.pop(get_current_user, None)

    try:
        yield
    finally:
        if inherited is not None:
            app.dependency_overrides[get_current_user] = inherited


@pytest.fixture
def decrypt_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    original = ProviderCredentialVault.decrypt

    def recording(self: ProviderCredentialVault, **kwargs: Any) -> Any:
        calls.append(1)

        return original(self, **kwargs)

    monkeypatch.setattr(ProviderCredentialVault, "decrypt", recording)

    return calls


@dataclass(frozen=True)
class Member:
    session_token: str
    csrf_token: str
    user_id: int


@dataclass(frozen=True)
class Seeded:
    workspace_id: int
    league_id: str
    administrator: Member
    spectator: Member


@pytest.fixture
def seed() -> Iterator[Callable[..., Seeded]]:
    created: list[int] = []

    def create(
        *,
        stage: OnboardingStage = OnboardingStage.INITIAL_SYNC_PENDING,
        access_mode: ProviderAccessMode = ProviderAccessMode.PUBLIC,
        registration_status: WorkspaceRegistrationStatus = WorkspaceRegistrationStatus.PROVISIONAL,
        installation_status: InstallationStatus = InstallationStatus.SETUP_IN_PROGRESS,
        with_claim: bool = True,
        verified: bool = True,
    ) -> Seeded:
        suffix = uuid4().hex[:12]
        league_id = str(100000 + uuid4().int % 800000)

        with SessionFactory() as session:
            workspace = Installation(
                application_name="Initial Sync League",
                slug=f"sync-{suffix}",
                normalized_slug=f"sync-{suffix}",
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                onboarding_stage=stage,
            )
            session.add(workspace)
            session.flush()
            created.append(workspace.id)

            encrypted = None
            key_version = None

            if access_mode is ProviderAccessMode.PRIVATE:
                key_version = 1
                encrypted = ProviderCredentialVault(keys={1: KEY}).encrypt(
                    provider=FantasyProvider.ESPN,
                    credentials=ProviderCredentials(
                        values={"swid": SYNTHETIC_SWID, "espn_s2": SYNTHETIC_S2}
                    ),
                    key_version=1,
                )

            session.add(
                ProviderConnection(
                    installation_id=workspace.id,
                    provider=FantasyProvider.ESPN,
                    display_name="ESPN",
                    is_active=True,
                    access_mode=access_mode,
                    provider_league_id=league_id,
                    provider_season_year=SEASON,
                    encrypted_credentials=encrypted,
                    credential_version=key_version,
                    last_validated_at=datetime.now(UTC),
                )
            )

            if with_claim:
                session.add(
                    ProviderLeagueClaim(
                        workspace_id=workspace.id,
                        provider=FantasyProvider.ESPN,
                        provider_league_id=league_id,
                        normalized_provider_league_id=league_id,
                    )
                )

            repository = AuthenticationRepository(session)
            administrator = repository.create_user(
                username=f"internal-admin-{suffix}",
                display_name="Sync Administrator",
                email=f"sync-{suffix}@example.com",
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC) if verified else None
            spectator = repository.create_user(
                username=f"internal-spectator-{suffix}",
                display_name="Sync Spectator",
                password="shared spectator passphrase",
                role=UserRole.SPECTATOR,
                workspace_id=workspace.id,
            )
            session.flush()
            sessions = SessionRepository(session)
            admin_session = sessions.create_session(user=administrator, lifetime_hours=1)
            spectator_session = sessions.create_session(user=spectator, lifetime_hours=1)
            session.commit()

            return Seeded(
                workspace_id=workspace.id,
                league_id=league_id,
                administrator=Member(
                    admin_session.session_token, admin_session.csrf_token, administrator.id
                ),
                spectator=Member(
                    spectator_session.session_token, spectator_session.csrf_token, spectator.id
                ),
            )

    yield create

    with SessionFactory() as session:
        for workspace_id in created:
            # seasons.provider_connection_id is ON DELETE RESTRICT, so remove
            # provider-derived league data before the workspace cascade.
            session.execute(
                text("DELETE FROM leagues WHERE installation_id = :id"), {"id": workspace_id}
            )
            session.execute(text("DELETE FROM installations WHERE id = :id"), {"id": workspace_id})

        session.commit()


def request(method: str, member: Member | None, body: dict[str, Any] | None = None):
    with TestClient(app) as client:
        headers = {"Origin": TRUSTED_ORIGIN}

        if member is not None:
            settings = get_settings()
            client.cookies.set(settings.session_cookie_name, member.session_token)
            client.cookies.set(csrf_cookie_name(settings), member.csrf_token)
            headers["X-CSRF-Token"] = member.csrf_token

        if method == "POST":
            return client.post(ENDPOINT, headers=headers, json=body)

        return client.get(ENDPOINT)


def start(member: Member | None, body: dict[str, Any] | None = None):
    return request("POST", member, body)


def progress(member: Member | None):
    return request("GET", member)


def stage_of(workspace_id: int) -> OnboardingStage | None:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)

        return None if workspace is None else workspace.onboarding_stage


def jobs_for(workspace_id: int) -> list[BackgroundJob]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.workspace_id == workspace_id)
                .order_by(BackgroundJob.id)
            )
        )
        session.expunge_all()

        return rows


def identity(workspace_id: int) -> tuple[list[League], list[Season], int | None]:
    with SessionFactory() as session:
        leagues = list(
            session.scalars(select(League).where(League.installation_id == workspace_id))
        )
        seasons = list(
            session.scalars(
                select(Season).join(League).where(League.installation_id == workspace_id)
            )
        )
        claim = session.scalar(
            select(ProviderLeagueClaim).where(ProviderLeagueClaim.workspace_id == workspace_id)
        )
        session.expunge_all()

        return leagues, seasons, (claim.league_id if claim is not None else None)


def count_for_season(model: Any, season_id: int) -> int:
    with SessionFactory() as session:
        return int(
            session.scalar(
                select(func.count()).select_from(model).where(model.season_id == season_id)
            )
            or 0
        )


def worker_settings():
    return get_settings().model_copy(
        update={
            "provider_credential_keys": SecretStr(f'{{"1": "{KEY}"}}'),
            "provider_credential_current_key_version": 1,
        }
    )


def run_worker(fake: FakeEspn, workspace_id: int):
    """Run one worker iteration, refusing to touch unrelated runnable jobs."""

    with SessionFactory() as session:
        unrelated = session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.workspace_id != workspace_id,
                BackgroundJob.status.in_(
                    (BackgroundJobStatus.PENDING, BackgroundJobStatus.RETRY_SCHEDULED)
                ),
            )
        )

    assert unrelated == 0, "Unrelated runnable jobs exist in the test database."

    return execute_one_background_job(
        session_factory=SessionFactory,
        worker_id="synthetic-worker",
        lease_duration=timedelta(minutes=5),
        handlers={
            BackgroundJobType.INITIAL_WORKSPACE_SYNC: InitialWorkspaceSyncHandler(
                session_factory=SessionFactory,
                settings=worker_settings(),
                registry=fake.registry(),
            )
        },
        heartbeat_interval=timedelta(minutes=4),
        retry_delays=(timedelta(seconds=1),),
        jitter_ratio=0,
    )


def assert_safe(text_body: str) -> None:
    for marker in (
        SYNTHETIC_SWID,
        SYNTHETIC_S2,
        "Traceback",
        "ProviderAuthenticationError",
        "ProviderResponseError",
        "payload",
        "lease",
        "claimed_by",
        "idempotency",
        "encrypted",
        "SECRET-PROVIDER-DETAIL",
    ):
        assert marker not in text_body


# --------------------------------------------------------------------------
# Authorization and stages
# --------------------------------------------------------------------------


def test_unauthenticated_requests_are_rejected() -> None:
    assert start(None).status_code == status.HTTP_401_UNAUTHORIZED
    assert progress(None).status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("verified", [True, False])
def test_spectators_and_unverified_administrators_are_rejected(
    seed: Callable[..., Seeded], verified: bool
) -> None:
    seeded = seed(verified=verified)
    member = seeded.spectator if verified else seeded.administrator

    for response in (start(member), progress(member)):
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["code"] == "initial_sync_forbidden"

    assert jobs_for(seeded.workspace_id) == []


@pytest.mark.parametrize(
    ("registration_status", "code"),
    [
        (WorkspaceRegistrationStatus.ACTIVE, "workspace_inactive"),
        (WorkspaceRegistrationStatus.SUSPENDED, "workspace_suspended"),
        (WorkspaceRegistrationStatus.DELETED, "workspace_inactive"),
    ],
)
def test_non_provisional_workspaces_are_rejected(
    seed: Callable[..., Seeded], registration_status: WorkspaceRegistrationStatus, code: str
) -> None:
    seeded = seed(
        registration_status=registration_status, installation_status=InstallationStatus.ACTIVE
    )

    response = start(seeded.administrator)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == code
    assert jobs_for(seeded.workspace_id) == []


START_STAGES = {
    OnboardingStage.INITIAL_SYNC_PENDING,
    OnboardingStage.INITIAL_SYNC_FAILED,
    OnboardingStage.INITIAL_SYNC_RUNNING,
}


@pytest.mark.parametrize(
    "stage", sorted(set(OnboardingStage) - START_STAGES - {OnboardingStage.SPECTATOR_SETUP_PENDING})
)
def test_unrelated_stages_are_rejected(seed: Callable[..., Seeded], stage: OnboardingStage) -> None:
    seeded = seed(stage=stage)

    for response in (start(seeded.administrator), progress(seeded.administrator)):
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == "onboarding_stage_invalid"

    assert stage_of(seeded.workspace_id) is stage
    assert jobs_for(seeded.workspace_id) == []


def test_spectator_setup_pending_may_read_progress_but_not_start(
    seed: Callable[..., Seeded],
) -> None:
    seeded = seed(stage=OnboardingStage.SPECTATOR_SETUP_PENDING)

    assert progress(seeded.administrator).status_code == status.HTTP_200_OK
    assert start(seeded.administrator).json()["code"] == "onboarding_stage_invalid"


@pytest.mark.parametrize(
    "field_name",
    ["workspace_id", "installation_id", "league_id", "season_id", "claim_id", "payload", "swid"],
)
def test_browser_supplied_fields_are_rejected(seed: Callable[..., Seeded], field_name: str) -> None:
    seeded = seed()

    assert start(seeded.administrator, {field_name: 1}).status_code == 422
    assert jobs_for(seeded.workspace_id) == []


def test_missing_claim_cannot_start(seed: Callable[..., Seeded]) -> None:
    seeded = seed(with_claim=False)

    response = start(seeded.administrator)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["code"] == "provider_target_unavailable"
    assert jobs_for(seeded.workspace_id) == []


# --------------------------------------------------------------------------
# Start request: queue only
# --------------------------------------------------------------------------


def test_start_only_queues_one_job(seed: Callable[..., Seeded], decrypt_calls: list[int]) -> None:
    seeded = seed(access_mode=ProviderAccessMode.PRIVATE)

    response = start(seeded.administrator, {})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["onboarding_stage"] == "initial_sync_running"
    assert set(body["job"]) == {
        "id",
        "status",
        "retryable",
        "detail",
        "created_at",
        "started_at",
        "completed_at",
        "failed_at",
    }
    assert body["job"]["status"] == "pending"
    assert_safe(response.text)

    [job] = jobs_for(seeded.workspace_id)
    assert job.job_type is BackgroundJobType.INITIAL_WORKSPACE_SYNC
    assert job.payload == {"trigger": "onboarding"}
    assert seeded.league_id not in str(job.payload)
    assert job.idempotency_key == initial_sync_idempotency_key(seeded.workspace_id)
    assert stage_of(seeded.workspace_id) is OnboardingStage.INITIAL_SYNC_RUNNING

    leagues, seasons, attached = identity(seeded.workspace_id)
    assert (leagues, seasons, attached) == ([], [], None)
    assert decrypt_calls == []


def test_repeated_start_returns_the_existing_job(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    first = start(seeded.administrator).json()["job"]["id"]
    second = start(seeded.administrator).json()["job"]["id"]

    assert first == second
    assert len(jobs_for(seeded.workspace_id)) == 1


def test_concurrent_starts_create_one_job(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    results: list[Any] = [None, None]
    barrier = threading.Barrier(2, timeout=10)

    def worker(index: int) -> None:
        barrier.wait()
        results[index] = start(seeded.administrator)

    threads = [threading.Thread(target=worker, args=(index,)) for index in (0, 1)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=30)

    assert [response.status_code for response in results] == [200, 200]
    assert results[0].json()["job"]["id"] == results[1].json()["job"]["id"]
    assert len(jobs_for(seeded.workspace_id)) == 1


def test_progress_is_workspace_isolated(seed: Callable[..., Seeded]) -> None:
    first = seed()
    second = seed()

    job_id = start(first.administrator).json()["job"]["id"]
    other = progress(second.administrator).json()

    assert other == {"onboarding_stage": "initial_sync_pending", "job": None}
    assert progress(first.administrator).json()["job"]["id"] == job_id


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------


def test_public_worker_creates_real_identity_and_advances(
    seed: Callable[..., Seeded], decrypt_calls: list[int]
) -> None:
    seeded = seed()
    fake = FakeEspn()
    start(seeded.administrator)

    result = run_worker(fake, seeded.workspace_id)

    assert result.succeeded is True
    [job] = jobs_for(seeded.workspace_id)
    assert job.status is BackgroundJobStatus.SUCCEEDED

    leagues, seasons, attached = identity(seeded.workspace_id)
    [league] = leagues
    [season] = seasons
    assert league.name == "Synthetic Saturday League"
    assert league.provider_league_id == seeded.league_id
    assert attached == league.id
    assert season.year == SEASON
    assert season.current_week == 1
    assert season.regular_season_weeks == 2
    assert season.status is SeasonStatus.COMPLETED
    assert count_for_season(Team, season.id) == 2
    assert count_for_season(Matchup, season.id) == 2

    # Public: no decryption and no credentials given to the adapter.
    assert decrypt_calls == []
    assert all("swid" not in values and "espn_s2" not in values for values in fake.built_with)
    assert fake.built_with[0]["league_id"] == seeded.league_id
    # The authoritative snapshot is fetched with no database connection open.
    assert fake.open_connections_at_first_fetch == [0]

    with SessionFactory() as session:
        workspace = session.get(Installation, seeded.workspace_id)
        assert workspace is not None
        assert workspace.onboarding_stage is OnboardingStage.SPECTATOR_SETUP_PENDING
        assert workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
        assert workspace.status is InstallationStatus.SETUP_IN_PROGRESS
        assert workspace.active_league_id is None
        assert workspace.active_season_id is None
        users = session.scalar(
            select(func.count()).select_from(User).where(User.workspace_id == seeded.workspace_id)
        )
        assert users == 2

    body = progress(seeded.administrator).json()
    assert body["onboarding_stage"] == "spectator_setup_pending"
    assert body["job"]["status"] == "succeeded"


def test_private_worker_decrypts_only_in_the_worker(
    seed: Callable[..., Seeded], decrypt_calls: list[int]
) -> None:
    seeded = seed(access_mode=ProviderAccessMode.PRIVATE)
    fake = FakeEspn()
    start(seeded.administrator)

    assert decrypt_calls == []

    assert run_worker(fake, seeded.workspace_id).succeeded is True
    assert decrypt_calls == [1]
    assert fake.built_with[0]["swid"] == SYNTHETIC_SWID
    assert fake.built_with[0]["espn_s2"] == SYNTHETIC_S2
    assert stage_of(seeded.workspace_id) is OnboardingStage.SPECTATOR_SETUP_PENDING

    for job in jobs_for(seeded.workspace_id):
        assert_safe(str(job.payload) + str(job.safe_error_message))


def test_transient_failure_retries_without_failing_onboarding(
    seed: Callable[..., Seeded],
) -> None:
    seeded = seed()
    fake = FakeEspn(
        fail_fetch_league=provider_error(
            ProviderUnavailableError, retryable=True, message="ESPN is unavailable."
        )
    )
    start(seeded.administrator)

    result = run_worker(fake, seeded.workspace_id)

    assert result.retry_scheduled is True
    assert jobs_for(seeded.workspace_id)[0].status is BackgroundJobStatus.RETRY_SCHEDULED
    assert stage_of(seeded.workspace_id) is OnboardingStage.INITIAL_SYNC_RUNNING
    # Failure before persistence leaves no League or Season.
    assert identity(seeded.workspace_id) == ([], [], None)

    body = progress(seeded.administrator).json()
    assert body["job"]["status"] == "retry_scheduled"
    assert body["onboarding_stage"] == "initial_sync_running"

    with SessionFactory() as session:
        session.execute(
            text(
                "UPDATE background_jobs SET available_at = now() - interval '1 second' "
                "WHERE workspace_id = :id"
            ),
            {"id": seeded.workspace_id},
        )
        session.commit()

    fake.fail_fetch_league = None
    assert run_worker(fake, seeded.workspace_id).succeeded is True
    assert stage_of(seeded.workspace_id) is OnboardingStage.SPECTATOR_SETUP_PENDING


def test_terminal_failure_marks_onboarding_failed_with_safe_detail(
    seed: Callable[..., Seeded],
) -> None:
    seeded = seed()
    fake = FakeEspn(
        fail_fetch_league=provider_error(
            ProviderAuthenticationError,
            retryable=False,
            message="SECRET-PROVIDER-DETAIL raw cookie",
        )
    )
    start(seeded.administrator)

    result = run_worker(fake, seeded.workspace_id)

    assert result.failed is True
    [job] = jobs_for(seeded.workspace_id)
    assert job.status is BackgroundJobStatus.FAILED
    assert stage_of(seeded.workspace_id) is OnboardingStage.INITIAL_SYNC_FAILED
    assert identity(seeded.workspace_id) == ([], [], None)

    response = progress(seeded.administrator)
    body = response.json()
    assert body["onboarding_stage"] == "initial_sync_failed"
    assert body["job"]["status"] == "failed"
    assert body["job"]["retryable"] is True
    assert body["job"]["detail"].startswith("ESPN no longer accepts")
    assert_safe(response.text)


def test_retry_after_failure_reuses_identity(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    fake = FakeEspn(
        fail_matchups=provider_error(
            ProviderResponseError, retryable=False, message="Malformed matchups."
        )
    )
    start(seeded.administrator)

    assert run_worker(fake, seeded.workspace_id).failed is True
    assert stage_of(seeded.workspace_id) is OnboardingStage.INITIAL_SYNC_FAILED

    # Identity was persisted from the snapshot; the importer rolled back its data.
    [league], [season], attached = identity(seeded.workspace_id)
    assert attached == league.id
    assert count_for_season(Team, season.id) == 0

    retry = start(seeded.administrator)
    assert retry.status_code == status.HTTP_200_OK
    assert retry.json()["job"]["id"] != jobs_for(seeded.workspace_id)[0].id

    fake.fail_matchups = None
    fake.league_name = "Synthetic Renamed League"
    assert run_worker(fake, seeded.workspace_id).succeeded is True

    leagues, seasons, attached_after = identity(seeded.workspace_id)
    assert [item.id for item in leagues] == [league.id]
    assert [item.id for item in seasons] == [season.id]
    assert attached_after == league.id
    assert leagues[0].name == "Synthetic Renamed League"
    assert [job.status for job in jobs_for(seeded.workspace_id)] == [
        BackgroundJobStatus.FAILED,
        BackgroundJobStatus.SUCCEEDED,
    ]
    assert stage_of(seeded.workspace_id) is OnboardingStage.SPECTATOR_SETUP_PENDING


def test_mismatched_snapshot_creates_nothing(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    fake = FakeEspn(returned_league_id="999999999")
    start(seeded.administrator)

    assert run_worker(fake, seeded.workspace_id).failed is True
    assert jobs_for(seeded.workspace_id)[0].safe_error_code == "provider_identity_mismatch"
    assert identity(seeded.workspace_id) == ([], [], None)
    assert stage_of(seeded.workspace_id) is OnboardingStage.INITIAL_SYNC_FAILED


def test_payload_with_secret_fields_is_rejected(seed: Callable[..., Seeded]) -> None:
    seeded = seed()
    start(seeded.administrator)

    with SessionFactory() as session:
        job = session.scalar(
            select(BackgroundJob).where(BackgroundJob.workspace_id == seeded.workspace_id)
        )
        assert job is not None
        job.payload = {"trigger": "onboarding", "espn_s2": "x"}
        session.commit()

    fake = FakeEspn()

    assert run_worker(fake, seeded.workspace_id).failed is True
    assert jobs_for(seeded.workspace_id)[0].safe_error_code == "invalid_job_payload"
    assert fake.built_with == []


def test_worker_only_touches_its_own_workspace(seed: Callable[..., Seeded]) -> None:
    mine = seed()
    other = seed()
    start(mine.administrator)

    assert run_worker(FakeEspn(), mine.workspace_id).succeeded is True

    assert identity(other.workspace_id) == ([], [], None)
    assert stage_of(other.workspace_id) is OnboardingStage.INITIAL_SYNC_PENDING


@pytest.mark.parametrize(
    ("attempt_count", "expected_status", "expected_stage"),
    [
        (3, BackgroundJobStatus.FAILED, OnboardingStage.INITIAL_SYNC_FAILED),
        (1, BackgroundJobStatus.RETRY_SCHEDULED, OnboardingStage.INITIAL_SYNC_RUNNING),
    ],
)
def test_lease_recovery_applies_the_matching_stage(
    seed: Callable[..., Seeded],
    attempt_count: int,
    expected_status: BackgroundJobStatus,
    expected_stage: OnboardingStage,
) -> None:
    seeded = seed()
    start(seeded.administrator)
    with SessionFactory() as session:
        job = BackgroundJobRepository(session).claim_next_available(
            worker_id="abandoned-worker",
            lease_duration=timedelta(minutes=1),
            now=datetime.now(UTC),
        )
        assert job is not None
        assert job.workspace_id == seeded.workspace_id
        assert job.status is BackgroundJobStatus.RUNNING
        # The worker vanished: its lease is long expired.
        job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=10)
        job.attempt_count = attempt_count
        session.commit()

    recover_expired_background_jobs_committed(session_factory=SessionFactory)

    [job] = jobs_for(seeded.workspace_id)
    assert job.status is expected_status
    assert stage_of(seeded.workspace_id) is expected_stage
