"""Critical League-Season synchronization behavior (ADR 0010).

Real PostgreSQL (``fantasy_what_if_test``) only. Every actor uses its own
Session and connection; races are ordered with threading events and
PostgreSQL lock observation, never sleeps. Providers are synthetic and
in-process; no test performs network I/O. Users, access rows, connections
and credentials are synthetic.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season, SyncStatus, Team
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    FantasySport,
    ProviderAccessMode,
    SeasonStatus,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.db.session import SessionFactory
from app.providers.domain import ProviderCredentials
from app.providers.registry import ProviderRegistry
from app.repositories.background_job_repository import BackgroundJobRepository
from app.services.admin_synchronization_jobs import enqueue_administrator_synchronization
from app.services.background_job_handlers import build_background_job_handlers
from app.services.background_job_lease_recovery import recover_expired_background_jobs_committed
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    execute_one_background_job,
)
from app.services.provider_sync import (
    SyncJobAuthority,
    execute_provider_sync,
    prepare_season_sync,
    record_sync_failure,
    synchronize_season,
)
from app.services.scheduled_synchronization_jobs import (
    ScheduledSynchronizationTarget,
    enqueue_scheduled_synchronization,
)
from app.services.season_sync_jobs import LEASE_EXPIRED_SYNC_MESSAGE, enqueue_season_sync
from tests.support.provider_sync_fixtures import (
    KEY,
    RAW_RESPONSE_MARKER,
    WAIT_SECONDS,
    WORKER,
    Harness,
    SyncGraph,
    SyntheticSeasonAdapter,
    canonical_state,
    claim,
    encrypt_synthetic_credentials,
    forbid_live_network,
    graph_factory,
    harness_context,
    run_in_thread,
    sync_runs,
    worker_settings,
)
from tests.support.workspace_deletion_graph import TEST_JOB_PRIORITY

LEASE = timedelta(minutes=5)


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


@pytest.fixture
def harness() -> Iterator[Harness]:
    with harness_context() as created:
        yield created


@pytest.fixture
def make_graph() -> Iterator[Callable[[], SyncGraph]]:
    with graph_factory() as create:
        yield create


# --------------------------------------------------------------------------
# Personal-account fixtures (no workspace)
# --------------------------------------------------------------------------


@dataclass
class PersonalSeason:
    league_id: int
    season_id: int
    provider_league_id: str


@dataclass
class Member:
    user_id: int
    email: str
    display_name: str
    connection_id: int | None
    swid: str
    espn_s2: str


@dataclass
class Personal:
    leagues: list[int] = field(default_factory=list)
    users: list[int] = field(default_factory=list)

    def season(self) -> PersonalSeason:
        provider_league_id = f"personal-{uuid4().hex[:12]}"

        with SessionFactory() as session:
            league = League(
                name="Personal Synthetic League",
                provider=FantasyProvider.ESPN,
                provider_league_id=provider_league_id,
                sport=FantasySport.FOOTBALL,
            )
            session.add(league)
            session.flush()
            season = Season(
                league_id=league.id,
                year=2026,
                current_week=1,
                regular_season_weeks=3,
                provider_season_id="2026",
                status=SeasonStatus.ACTIVE,
            )
            session.add(season)
            session.commit()
            self.leagues.append(league.id)

            return PersonalSeason(league.id, season.id, provider_league_id)

    def member(
        self,
        target: PersonalSeason,
        *,
        proof: UserSeasonProofMode = UserSeasonProofMode.PRIVATE_CREDENTIALS,
        validated_at: datetime | None = None,
        connection_owner: int | None = None,
        connection_active: bool = True,
        access_status: UserSeasonAccessStatus = UserSeasonAccessStatus.ACTIVE,
        user_active: bool = True,
        workspace_connection_id: int | None = None,
    ) -> Member:
        suffix = uuid4().hex[:10]
        email = f"member-{suffix}@example.invalid"
        display_name = f"Member Display {suffix}"
        swid = f"{{MEMBER-SWID-{suffix}}}"
        espn_s2 = f"MEMBER-S2-{suffix}"

        with SessionFactory() as session:
            user = User(
                username=f"member-{suffix}",
                display_name=display_name,
                email=email,
                normalized_email=email,
                password_hash="$argon2id$synthetic",
                role=UserRole.SPECTATOR,
                is_active=user_active,
            )
            session.add(user)
            session.flush()
            self.users.append(user.id)
            connection_id = workspace_connection_id

            if proof is UserSeasonProofMode.PRIVATE_CREDENTIALS and connection_id is None:
                connection = ProviderConnection(
                    user_id=connection_owner or user.id,
                    provider=FantasyProvider.ESPN,
                    display_name=f"Personal {suffix}",
                    encrypted_credentials=encrypt_synthetic_credentials(
                        target.provider_league_id, swid=swid, espn_s2=espn_s2
                    ),
                    credential_version=1,
                    is_active=connection_active,
                    provider_league_id=target.provider_league_id[:32],
                    provider_season_year=2026,
                    access_mode=ProviderAccessMode.PRIVATE,
                    last_validated_at=validated_at,
                )
                session.add(connection)
                session.flush()
                connection_id = connection.id

            session.add(
                UserLeagueSeasonAccess(
                    user_id=user.id,
                    season_id=target.season_id,
                    provider_connection_id=(
                        connection_id if proof is UserSeasonProofMode.PRIVATE_CREDENTIALS else None
                    ),
                    proof_mode=proof,
                    status=access_status,
                    verified_at=datetime.now(UTC),
                )
            )
            session.commit()

        return Member(user.id, email, display_name, connection_id, swid, espn_s2)

    def cleanup(self) -> None:
        with SessionFactory() as session:
            season_ids = list(
                session.scalars(select(Season.id).where(Season.league_id.in_(self.leagues)))
            )
            session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.idempotency_key.in_(
                        [f"league-season-sync:{season_id}" for season_id in season_ids]
                    )
                )
            )
            session.execute(delete(League).where(League.id.in_(self.leagues)))
            session.execute(
                delete(ProviderConnection).where(ProviderConnection.user_id.in_(self.users))
            )
            session.execute(delete(User).where(User.id.in_(self.users)))
            session.commit()


@pytest.fixture
def personal() -> Iterator[Personal]:
    created = Personal()
    yield created
    created.cleanup()


class RecordingProvider:
    """Registry factory that records the credential names each build received."""

    def __init__(self, adapter: SyntheticSeasonAdapter) -> None:
        self.adapter = adapter
        self.credentials: list[dict[str, str]] = []

    def registry(self) -> ProviderRegistry:
        registry = ProviderRegistry()
        registry.register(FantasyProvider.ESPN, self._create)
        return registry

    def _create(self, credentials: ProviderCredentials) -> SyntheticSeasonAdapter:
        self.credentials.append(dict(credentials.values))
        return self.adapter


def adapter(
    target: PersonalSeason | SyncGraph, dataset: str = "alpha", **kwargs: Any
) -> SyntheticSeasonAdapter:
    return SyntheticSeasonAdapter(
        provider_league_id=target.provider_league_id, dataset=dataset, **kwargs
    )


def sync(target: PersonalSeason | SyncGraph, recording: RecordingProvider, factory=SessionFactory):
    return synchronize_season(
        session_factory=factory,
        season_id=target.season_id,
        settings=worker_settings(),
        registry=recording.registry(),
    )


def season_row(season_id: int) -> Season:
    with SessionFactory() as session:
        season = session.get(Season, season_id)
        assert season is not None
        session.expunge(season)
        return season


def team_ids(season_id: int) -> list[str]:
    with SessionFactory() as session:
        return sorted(
            session.scalars(select(Team.provider_team_id).where(Team.season_id == season_id))
        )


def set_access(member: Member, season_id: int, **values: Any) -> None:
    with SessionFactory() as session:
        session.execute(
            update(UserLeagueSeasonAccess)
            .where(
                UserLeagueSeasonAccess.user_id == member.user_id,
                UserLeagueSeasonAccess.season_id == season_id,
            )
            .values(**values)
        )
        session.commit()


def run_worker(harness_factory, recording: RecordingProvider):
    return execute_one_background_job(
        session_factory=harness_factory,
        worker_id=WORKER,
        lease_duration=LEASE,
        heartbeat_interval=timedelta(minutes=4),
        handlers=build_background_job_handlers(
            session_factory=harness_factory,
            settings=worker_settings(),
            registry=recording.registry(),
        ),
        jitter_ratio=0,
    )


def enqueue(season_id: int) -> int:
    with SessionFactory() as session:
        result = enqueue_season_sync(session, season_id=season_id, priority=TEST_JOB_PRIORITY)
        session.commit()
        return result.job.id


def job_row(job_id: int) -> BackgroundJob:
    with SessionFactory() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        session.expunge(job)
        return job


# --------------------------------------------------------------------------
# Credential selection
# --------------------------------------------------------------------------


def test_public_season_syncs_anonymously_and_selects_no_connection(personal: Personal) -> None:
    target = personal.season()
    personal.member(target, proof=UserSeasonProofMode.PUBLIC)
    private = personal.member(target, validated_at=datetime.now(UTC))
    recording = RecordingProvider(adapter(target))

    result = sync(target, recording)

    assert result.superseded is False
    assert recording.credentials == [{"league_id": target.provider_league_id, "season": "2026"}]
    assert private.swid not in str(recording.credentials)
    [run] = sync_runs(target.season_id)
    assert run.status is SyncStatus.SUCCEEDED and run.provider_connection_id is None
    season = season_row(target.season_id)
    assert season.data_version == 1
    assert season.sync_state is SeasonSyncState.FRESH
    assert season.last_successful_sync_at == season.last_synced_at is not None
    assert season.last_sync_failure_code is None


def test_private_season_selects_only_eligible_connection_deterministically(
    personal: Personal,
) -> None:
    target = personal.season()
    now = datetime.now(UTC)
    future = now + timedelta(days=1)
    outsider = personal.member(personal.season(), validated_at=future)

    personal.member(target, validated_at=now - timedelta(days=1))
    chosen = personal.member(target, validated_at=now)
    personal.member(target, validated_at=None)
    # Each of these would win on recency if it were eligible.
    personal.member(
        target, validated_at=future, access_status=UserSeasonAccessStatus.REVERIFICATION_REQUIRED
    )
    personal.member(target, validated_at=future, access_status=UserSeasonAccessStatus.REVOKED)
    personal.member(target, validated_at=future, connection_active=False)
    personal.member(target, validated_at=future, user_active=False)
    # Access row pointing at another user's connection: never usable proof.
    personal.member(target, workspace_connection_id=outsider.connection_id)
    recording = RecordingProvider(adapter(target))

    sync(target, recording)

    [credentials] = recording.credentials
    assert credentials["swid"] == chosen.swid
    assert credentials["espn_s2"] == chosen.espn_s2
    assert credentials["league_id"] == target.provider_league_id
    assert credentials["season"] == "2026"
    [run] = sync_runs(target.season_id)
    assert run.provider_connection_id is None


def test_equal_validation_time_falls_back_to_lowest_connection_id(personal: Personal) -> None:
    target = personal.season()
    validated = datetime.now(UTC)
    first = personal.member(target, validated_at=validated)
    personal.member(target, validated_at=validated)
    recording = RecordingProvider(adapter(target))

    sync(target, recording)

    assert recording.credentials[0]["swid"] == first.swid


def test_more_than_one_eligible_connection_is_not_a_failure(personal: Personal) -> None:
    target = personal.season()
    for _ in range(3):
        personal.member(target, validated_at=datetime.now(UTC))

    assert sync(target, RecordingProvider(adapter(target))).superseded is False


@pytest.mark.parametrize("prior_data", [False, True], ids=["never-synced", "prior-data"])
def test_no_eligible_connection_records_reverification_without_fetching(
    personal: Personal, prior_data: bool
) -> None:
    target = personal.season()
    member = personal.member(target, validated_at=datetime.now(UTC))

    if prior_data:
        sync(target, RecordingProvider(adapter(target, "kept")))

    set_access(member, target.season_id, status=UserSeasonAccessStatus.REVOKED)
    before = team_ids(target.season_id)
    blocked = adapter(target, "blocked")
    recording = RecordingProvider(blocked)

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(target, recording)

    assert raised.value.safe_error_code == "provider_reverification_required"
    assert recording.credentials == [] and blocked.calls == []
    assert team_ids(target.season_id) == before
    season = season_row(target.season_id)
    assert season.data_version == (1 if prior_data else 0)
    assert season.sync_state is (SeasonSyncState.STALE if prior_data else SeasonSyncState.FAILED)
    assert season.last_sync_failure_code == "provider_reverification_required"


def test_workspace_connection_never_authorizes_a_personal_season(
    personal: Personal, make_graph: Callable[[], SyncGraph]
) -> None:
    graph = make_graph()
    target = personal.season()

    # A Season row pointing at a workspace connection is not personal proof.
    with SessionFactory() as session:
        session.execute(
            update(Season)
            .where(Season.id == target.season_id)
            .values(provider_connection_id=graph.connection_id)
        )
        session.commit()

    recording = RecordingProvider(adapter(target))

    try:
        with pytest.raises(BackgroundJobExecutionError) as raised:
            sync(target, recording)
    finally:
        with SessionFactory() as session:
            session.execute(
                update(Season)
                .where(Season.id == target.season_id)
                .values(provider_connection_id=None)
            )
            session.commit()

    assert raised.value.safe_error_code == "provider_reverification_required"
    assert recording.credentials == []


def test_personal_access_disables_the_workspace_fallback(
    personal: Personal, make_graph: Callable[[], SyncGraph]
) -> None:
    graph = make_graph()
    graph_target = PersonalSeason(graph.league_id, graph.season_id, graph.provider_league_id)
    personal.member(graph_target, connection_active=False)
    recording = RecordingProvider(adapter(graph))

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(graph, recording)

    assert raised.value.safe_error_code == "provider_reverification_required"
    assert recording.credentials == []
    assert graph.swid not in str(recording.credentials)


# --------------------------------------------------------------------------
# Access and connection changes during the fetch
# --------------------------------------------------------------------------


def _deactivate_connection(member: Member, season_id: int) -> None:
    with SessionFactory() as session:
        session.execute(
            update(ProviderConnection)
            .where(ProviderConnection.id == member.connection_id)
            .values(is_active=False)
        )
        session.commit()


def _replace_credentials(member: Member, season_id: int) -> None:
    with SessionFactory() as session:
        connection = session.get(ProviderConnection, member.connection_id)
        assert connection is not None
        connection.encrypted_credentials = encrypt_synthetic_credentials(
            "replaced", swid="{REPLACED-SWID}", espn_s2="REPLACED-S2"
        )
        session.commit()


def _revoke_access(member: Member, season_id: int) -> None:
    set_access(member, season_id, status=UserSeasonAccessStatus.REVOKED)


def _deactivate_user(member: Member, season_id: int) -> None:
    with SessionFactory() as session:
        session.execute(update(User).where(User.id == member.user_id).values(is_active=False))
        session.commit()


def _delete_owner(member: Member, season_id: int) -> None:
    with SessionFactory() as session:
        session.execute(
            delete(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.user_id == member.user_id)
        )
        session.execute(
            delete(ProviderConnection).where(ProviderConnection.user_id == member.user_id)
        )
        session.execute(delete(User).where(User.id == member.user_id))
        session.commit()


@pytest.mark.parametrize(
    "change",
    [_deactivate_connection, _replace_credentials, _revoke_access, _deactivate_user, _delete_owner],
    ids=[
        "connection-invalid",
        "credentials-replaced",
        "access-revoked",
        "user-inactive",
        "owner-deleted",
    ],
)
def test_selected_connection_change_during_fetch_blocks_persistence_and_retry_recovers(
    harness: Harness, personal: Personal, change: Callable[[Member, int], None]
) -> None:
    target = personal.season()
    fallback = personal.member(target, validated_at=datetime.now(UTC) - timedelta(days=1))
    selected = personal.member(target, validated_at=datetime.now(UTC))
    job_id = enqueue(target.season_id)

    def change_during_fetch(call: str) -> None:
        if call == "fetch_matchups":
            change(selected, target.season_id)

    first = RecordingProvider(adapter(target, "first", before_call=change_during_fetch))
    outcome = run_worker(harness.factory, first)

    assert outcome.job_id == job_id and outcome.retry_scheduled is True
    assert first.credentials[0]["swid"] == selected.swid
    assert team_ids(target.season_id) == []
    job = job_row(job_id)
    assert job.safe_error_code == "provider_connection_changed"
    season = season_row(target.season_id)
    assert season.data_version == 0
    assert season.sync_state is SeasonSyncState.FAILED
    assert season.last_sync_failure_code == "provider_connection_changed"

    with SessionFactory() as session:
        session.execute(
            update(BackgroundJob).where(BackgroundJob.id == job_id).values(available_at=func.now())
        )
        session.commit()

    second = RecordingProvider(adapter(target, "second"))
    assert run_worker(harness.factory, second).succeeded is True
    # Replaced credentials keep the same owner's connection eligible; every
    # other change hands the retry to the remaining eligible connection.
    expected = "{REPLACED-SWID}" if change is _replace_credentials else fallback.swid
    assert second.credentials[0]["swid"] == expected
    assert team_ids(target.season_id)[0].startswith("second-")
    assert season_row(target.season_id).data_version == 1

    # Neither the job, the runs nor the Season says whose credentials ran.
    job = job_row(job_id)
    haystack = " ".join(
        str(value)
        for value in (
            job.payload,
            job.safe_error_code,
            job.safe_error_message,
            job.progress,
            *(
                (run.provider_request_metadata, run.safe_error_message, run.provider_connection_id)
                for run in sync_runs(target.season_id)
            ),
            season_row(target.season_id).last_sync_failure_code,
        )
    )
    for member in (selected, fallback):
        for marker in (member.email, member.display_name, member.swid, member.espn_s2):
            assert marker not in haystack
    assert job.payload == {"version": 1, "season_id": target.season_id}
    assert all(run.provider_connection_id is None for run in sync_runs(target.season_id))


def test_removing_all_access_during_fetch_blocks_persistence_and_keeps_prior_data(
    personal: Personal,
) -> None:
    target = personal.season()
    member = personal.member(target, validated_at=datetime.now(UTC))
    sync(target, RecordingProvider(adapter(target, "kept")))
    kept = team_ids(target.season_id)

    def revoke(call: str) -> None:
        if call == "fetch_league":
            _revoke_access(member, target.season_id)

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(target, RecordingProvider(adapter(target, "lost", before_call=revoke)))

    assert raised.value.safe_error_code == "provider_connection_changed"
    assert team_ids(target.season_id) == kept
    season = season_row(target.season_id)
    assert season.data_version == 1
    assert season.sync_state is SeasonSyncState.STALE

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(target, RecordingProvider(adapter(target, "never")))

    assert raised.value.safe_error_code == "provider_reverification_required"
    assert team_ids(target.season_id) == kept


def test_public_to_private_change_during_fetch_blocks_persistence(personal: Personal) -> None:
    target = personal.season()
    public = personal.member(target, proof=UserSeasonProofMode.PUBLIC)

    def go_private(call: str) -> None:
        if call == "fetch_matchups":
            _revoke_access(public, target.season_id)
            personal.member(target, validated_at=datetime.now(UTC))

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(target, RecordingProvider(adapter(target, before_call=go_private)))

    assert raised.value.safe_error_code == "provider_access_mode_changed"
    assert raised.value.retryable is True
    assert team_ids(target.season_id) == []
    assert season_row(target.season_id).data_version == 0


def test_private_to_public_change_during_fetch_blocks_persistence(personal: Personal) -> None:
    target = personal.season()
    personal.member(target, validated_at=datetime.now(UTC))

    def go_public(call: str) -> None:
        if call == "fetch_matchups":
            personal.member(target, proof=UserSeasonProofMode.PUBLIC)

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(target, RecordingProvider(adapter(target, before_call=go_public)))

    assert raised.value.safe_error_code == "provider_access_mode_changed"
    assert team_ids(target.season_id) == []


def test_workspace_gaining_personal_access_during_fetch_blocks_fallback_persistence(
    personal: Personal, make_graph: Callable[[], SyncGraph]
) -> None:
    graph = make_graph()
    before = canonical_state(graph)
    graph_target = PersonalSeason(graph.league_id, graph.season_id, graph.provider_league_id)

    def add_personal(call: str) -> None:
        if call == "fetch_matchups":
            personal.member(graph_target, validated_at=datetime.now(UTC))

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(graph, RecordingProvider(adapter(graph, before_call=add_personal)))

    assert raised.value.safe_error_code == "provider_access_mode_changed"
    assert canonical_state(graph) == before


def test_workspace_claim_removed_during_fetch_blocks_fallback_persistence(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = canonical_state(graph)

    def remove_claim(call: str) -> None:
        if call == "fetch_matchups":
            with SessionFactory() as session:
                session.execute(
                    delete(ProviderLeagueClaim).where(
                        ProviderLeagueClaim.workspace_id == graph.workspace_id
                    )
                )
                session.commit()

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(graph, RecordingProvider(adapter(graph, before_call=remove_claim)))

    # installation_id is unchanged, but without the claim the workspace
    # credential no longer authorizes persistence.
    assert raised.value.safe_error_code == "provider_connection_changed"
    assert canonical_state(graph) == before


# --------------------------------------------------------------------------
# Transaction boundaries on the personal path
# --------------------------------------------------------------------------


def test_private_provider_io_holds_no_database_state_and_locks_only_during_persistence(
    harness: Harness, personal: Personal
) -> None:
    target = personal.season()
    personal.member(target, validated_at=datetime.now(UTC))
    observed: list[str] = []

    def observe(call: str) -> None:
        harness.assert_no_database_state(target.season_id)
        harness.recorder.log(f"provider:{call}")
        observed.append(call)

    sync(target, RecordingProvider(adapter(target, before_call=observe)), factory=harness.factory)

    assert observed == ["fetch_league", "fetch_matchups", "fetch_weekly_rosters"]
    recorder = harness.recorder
    assert recorder.kinds().count("advisory_lock") == 1
    assert recorder.index("advisory_lock") > recorder.index("provider:fetch_weekly_rosters")
    # A personal Season never locks a workspace row.
    assert "workspace_lock" not in recorder.kinds()
    assert harness.season_advisory_lock_count(target.season_id) == 0


# --------------------------------------------------------------------------
# One active job per Season
# --------------------------------------------------------------------------


def test_concurrent_enqueue_callers_create_one_active_job(
    harness: Harness, personal: Personal
) -> None:
    target = personal.season()
    first = SessionFactory()

    try:
        created = enqueue_season_sync(first, season_id=target.season_id)
        assert created.existing is False

        def second_caller():  # noqa: ANN202
            with harness.factory() as session:
                result = enqueue_season_sync(session, season_id=target.season_id)
                session.commit()
                return result.job.id, result.existing

        thread, outcome = run_in_thread(second_caller)
        # The second caller waits on the first caller's Season lock.
        harness.wait_until_blocked()
        first.commit()
        thread.join(WAIT_SECONDS)
    finally:
        first.close()

    assert outcome == [(created.job.id, True)]

    with SessionFactory() as session:
        active = session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key == f"league-season-sync:{target.season_id}",
                BackgroundJob.status.in_(
                    (BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING)
                ),
            )
        ).all()
    assert [job.id for job in active] == [created.job.id]
    assert active[0].workspace_id is None
    assert season_row(target.season_id).sync_state is SeasonSyncState.QUEUED


def test_manual_scheduled_and_direct_callers_share_one_workspace_season_job(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()

    with SessionFactory() as session:
        manual = enqueue_administrator_synchronization(
            session, workspace_id=graph.workspace_id, full_sync=True
        )
        incremental = enqueue_administrator_synchronization(
            session, workspace_id=graph.workspace_id, full_sync=False
        )
        scheduled = enqueue_scheduled_synchronization(
            session,
            target=ScheduledSynchronizationTarget(
                workspace_id=graph.workspace_id, season_id=graph.season_id
            ),
            scheduled_for=datetime.now(UTC),
        )
        direct = enqueue_season_sync(session, season_id=graph.season_id)
        session.commit()

    assert manual.existing is False
    assert {manual.job_id, incremental.job_id, scheduled.job_id, direct.job.id} == {manual.job_id}
    job = job_row(manual.job_id)
    assert job.job_type is BackgroundJobType.LEAGUE_SEASON_SYNC
    # Transitional: the job stays visible to its workspace's status reads.
    assert job.workspace_id == graph.workspace_id
    assert job.payload == {"version": 1, "season_id": graph.season_id}


def test_database_rejects_a_second_active_job_or_a_mismatched_key(personal: Personal) -> None:
    target = personal.season()
    enqueue(target.season_id)

    for key in (
        f"league-season-sync:{target.season_id}",
        f"league-season-sync:{target.season_id}x",
    ):
        with SessionFactory() as session:
            session.add(
                BackgroundJob(
                    workspace_id=None,
                    job_type=BackgroundJobType.LEAGUE_SEASON_SYNC,
                    idempotency_key=key,
                    payload_version=1,
                    payload={"version": 1, "season_id": target.season_id},
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()


def test_legacy_job_adapter_never_syncs_and_enqueues_its_payload_season(
    harness: Harness, make_graph: Callable[[], SyncGraph]
) -> None:
    graph = make_graph()

    with SessionFactory() as session:
        legacy = BackgroundJobRepository(session).enqueue(
            workspace_id=graph.workspace_id,
            job_type=BackgroundJobType.FULL_SEASON_SYNC,
            payload={"season_id": graph.season_id, "full_sync": True, "trigger": "legacy"},
            idempotency_key=f"legacy-sync:{uuid4().hex}",
            priority=TEST_JOB_PRIORITY + 1,
        )
        session.commit()

    recording = RecordingProvider(adapter(graph))
    assert run_worker(harness.factory, recording).job_id == legacy.job.id
    assert recording.credentials == []
    assert job_row(legacy.job.id).status is BackgroundJobStatus.SUCCEEDED

    with SessionFactory() as session:
        season_job = BackgroundJobRepository(session).get_active_by_idempotency_key(
            f"league-season-sync:{graph.season_id}"
        )
    assert season_job is not None and season_job.workspace_id == graph.workspace_id


# --------------------------------------------------------------------------
# Lease loss, restart and stale attempts
# --------------------------------------------------------------------------


def _expire_lease(job_id: int) -> None:
    with SessionFactory() as session:
        session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()


def test_zombie_worker_after_lease_recovery_loses_to_the_new_attempt(
    harness: Harness, personal: Personal
) -> None:
    target = personal.season()
    personal.member(target, validated_at=datetime.now(UTC))
    job_id = enqueue(target.season_id)
    zombie_snapshot = claim(job_id)
    zombie_fetching = threading.Event()
    release_zombie = threading.Event()

    def hold(call: str) -> None:
        if call == "fetch_matchups":
            zombie_fetching.set()
            assert release_zombie.wait(WAIT_SECONDS)

    zombie_handler = build_background_job_handlers(
        session_factory=harness.factory,
        settings=worker_settings(),
        registry=RecordingProvider(adapter(target, "zombie", before_call=hold)).registry(),
    )[BackgroundJobType.LEAGUE_SEASON_SYNC]
    thread, zombie_outcome = run_in_thread(lambda: zombie_handler(zombie_snapshot))
    assert zombie_fetching.wait(WAIT_SECONDS)

    _expire_lease(job_id)
    recovered = recover_expired_background_jobs_committed(
        session_factory=SessionFactory, retry_delay=timedelta(0)
    )
    assert recovered.retry_scheduled_count >= 1
    assert season_row(target.season_id).sync_state is SeasonSyncState.QUEUED
    assert sync_runs(target.season_id)[-1].safe_error_message == LEASE_EXPIRED_SYNC_MESSAGE

    assert run_worker(harness.factory, RecordingProvider(adapter(target, "fresh"))).succeeded

    release_zombie.set()
    thread.join(WAIT_SECONDS)
    [error] = zombie_outcome
    assert isinstance(error, BackgroundJobExecutionError)
    assert error.safe_error_code == "job_authority_lost"

    season = season_row(target.season_id)
    assert season.data_version == 1
    assert season.sync_state is SeasonSyncState.FRESH
    assert all(team.startswith("fresh-") for team in team_ids(target.season_id))
    statuses = [run.status for run in sync_runs(target.season_id)]
    assert statuses == [SyncStatus.FAILED, SyncStatus.SUCCEEDED]


@pytest.mark.parametrize(
    ("exhausted", "prior_data", "expected"),
    [
        (False, False, SeasonSyncState.QUEUED),
        (True, False, SeasonSyncState.FAILED),
        (True, True, SeasonSyncState.STALE),
    ],
    ids=["retrying", "exhausted-no-data", "exhausted-prior-data"],
)
def test_restart_while_syncing_reaches_a_bounded_recoverable_state(
    harness: Harness,
    personal: Personal,
    exhausted: bool,
    prior_data: bool,
    expected: SeasonSyncState,
) -> None:
    target = personal.season()
    personal.member(target, validated_at=datetime.now(UTC))

    if prior_data:
        sync(target, RecordingProvider(adapter(target, "kept")))

    job_id = enqueue(target.season_id)
    snapshot = claim(job_id)

    # The worker prepares (syncing) and then the process dies before fetching.
    prepare_season_sync(
        session_factory=SessionFactory,
        season_id=target.season_id,
        settings=worker_settings(),
        registry=RecordingProvider(adapter(target)).registry(),
        job_authority=SyncJobAuthority(job_id=job_id, attempt_count=snapshot.attempt_count),
    )
    assert season_row(target.season_id).sync_state is SeasonSyncState.SYNCING

    if exhausted:
        with SessionFactory() as session:
            session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .values(maximum_attempts=snapshot.attempt_count)
            )
            session.commit()

    _expire_lease(job_id)
    recover_expired_background_jobs_committed(
        session_factory=SessionFactory, retry_delay=timedelta(0)
    )

    season = season_row(target.season_id)
    assert season.sync_state is expected
    assert season.data_version == (1 if prior_data else 0)
    assert sync_runs(target.season_id)[-1].status is SyncStatus.FAILED
    if exhausted:
        assert season.last_sync_failure_code == "worker_lease_expired"
        assert job_row(job_id).status is BackgroundJobStatus.FAILED
    else:
        assert run_worker(harness.factory, RecordingProvider(adapter(target, "after"))).succeeded
        assert season_row(target.season_id).sync_state is SeasonSyncState.FRESH


def test_stale_attempt_failure_never_overwrites_a_newer_attempt(personal: Personal) -> None:
    target = personal.season()
    personal.member(target, validated_at=datetime.now(UTC))

    def prepare(dataset: str):  # noqa: ANN202
        return prepare_season_sync(
            session_factory=SessionFactory,
            season_id=target.season_id,
            settings=worker_settings(),
            registry=RecordingProvider(adapter(target, dataset)).registry(),
        )

    older_plan, _ = prepare("older")
    newer_plan, newer_provider = prepare("newer")

    # The older attempt fails while the newer one is still syncing.
    record_sync_failure(SessionFactory, plan=older_plan, error=RuntimeError("late"))
    assert season_row(target.season_id).sync_state is SeasonSyncState.SYNCING
    assert season_row(target.season_id).last_sync_failure_code is None

    execute_provider_sync(session_factory=SessionFactory, plan=newer_plan, provider=newer_provider)
    record_sync_failure(SessionFactory, plan=older_plan, error=RuntimeError("later"))

    season = season_row(target.season_id)
    assert season.sync_state is SeasonSyncState.FRESH
    assert season.data_version == 1
    assert season.last_sync_failure_code is None


def test_data_version_increments_once_per_successful_persistence(personal: Personal) -> None:
    target = personal.season()
    personal.member(target, proof=UserSeasonProofMode.PUBLIC)

    for expected, dataset in enumerate(("one", "two", "three"), start=1):
        sync(target, RecordingProvider(adapter(target, dataset)))
        assert season_row(target.season_id).data_version == expected

    failing = adapter(target, "four", fail_on="fetch_matchups")
    with pytest.raises(Exception):  # noqa: B017, PT011 - provider failure type is incidental
        sync(target, RecordingProvider(failing))

    season = season_row(target.season_id)
    assert season.data_version == 3
    assert season.sync_state is SeasonSyncState.STALE
    assert season.last_sync_failure_code == "unavailable"
    assert not any(team.startswith("four-") for team in team_ids(target.season_id))


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


def test_synchronization_writes_only_the_payload_season(personal: Personal) -> None:
    first = personal.season()
    second = personal.season()
    member = personal.member(first, validated_at=datetime.now(UTC))
    with SessionFactory() as session:
        session.add(
            UserLeagueSeasonAccess(
                user_id=member.user_id,
                season_id=second.season_id,
                provider_connection_id=member.connection_id,
                proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
                verified_at=datetime.now(UTC),
            )
        )
        session.commit()

    sync(first, RecordingProvider(adapter(first)))

    assert team_ids(first.season_id)
    assert team_ids(second.season_id) == []
    untouched = season_row(second.season_id)
    assert untouched.data_version == 0 and untouched.sync_state is None
    assert sync_runs(second.season_id) == []


def test_workspace_job_for_a_non_active_season_never_syncs_the_active_season(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()

    with SessionFactory() as session:
        other = Season(
            league_id=graph.league_id,
            year=2025,
            current_week=1,
            regular_season_weeks=3,
            provider_season_id="2025",
            status=SeasonStatus.COMPLETED,
            provider_connection_id=graph.connection_id,
        )
        session.add(other)
        session.commit()
        other_id = other.id

    active_before = canonical_state(graph)
    recording = RecordingProvider(adapter(graph))

    try:
        with pytest.raises(BackgroundJobExecutionError) as raised:
            synchronize_season(
                session_factory=SessionFactory,
                season_id=other_id,
                settings=worker_settings(),
                registry=recording.registry(),
            )

        assert raised.value.safe_error_code == "inactive_season_job"
        assert recording.credentials == []
        assert canonical_state(graph) == active_before
        assert season_row(other_id).last_sync_failure_code == "inactive_season_job"
    finally:
        with SessionFactory() as session:
            session.execute(delete(Season).where(Season.id == other_id))
            session.commit()


# --------------------------------------------------------------------------
# Non-disclosure
# --------------------------------------------------------------------------


def test_no_credentials_or_owner_identity_leak_through_jobs_runs_or_logs(
    harness: Harness, personal: Personal, caplog: pytest.LogCaptureFixture
) -> None:
    target = personal.season()
    member = personal.member(target, validated_at=datetime.now(UTC))
    failing_job = enqueue(target.season_id)

    with caplog.at_level(logging.DEBUG):
        outcome = run_worker(
            harness.factory,
            RecordingProvider(adapter(target, fail_on="fetch_league")),
        )
        assert outcome.job_id == failing_job

        with SessionFactory() as session:
            session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == failing_job)
                .values(available_at=func.now())
            )
            session.commit()

        result = run_worker(harness.factory, RecordingProvider(adapter(target)))
        assert result.succeeded is True

    job = job_row(failing_job)
    captured = [
        str(job.payload),
        str(job.safe_error_code),
        str(job.safe_error_message),
        repr(outcome),
        repr(result),
        str(season_row(target.season_id).last_sync_failure_code),
        caplog.text,
    ]
    for run in sync_runs(target.season_id):
        captured.extend((str(run.provider_request_metadata), str(run.safe_error_message)))
        assert run.provider_connection_id is None
    haystack = "\n".join(captured)

    for marker in (
        member.swid,
        member.espn_s2,
        member.email,
        member.display_name,
        "espn_s2",
        "swid",
        "user_id",
        "provider_connection_id",
        RAW_RESPONSE_MARKER,
        KEY,
    ):
        assert marker not in haystack
    assert set(job.payload) == {"version", "season_id"}


def test_installation_workspace_is_derived_not_supplied(
    make_graph: Callable[[], SyncGraph], personal: Personal
) -> None:
    graph = make_graph()
    target = personal.season()

    assert job_row(enqueue(graph.season_id)).workspace_id == graph.workspace_id
    assert job_row(enqueue(target.season_id)).workspace_id is None

    with SessionFactory() as session:
        assert session.get(Installation, graph.workspace_id) is not None


# --------------------------------------------------------------------------
# After final workspace deletion (ADR 0010 compatibility boundary)
# --------------------------------------------------------------------------


@pytest.fixture
def workspace_season(personal: Personal) -> Iterator[Callable[..., PersonalSeason]]:
    """A workspace-created League/Season, optionally with personal access, then deleted."""

    from tests.support.workspace_deletion_graph import (
        cleanup_graphs,
        create_deletion_graph,
        enqueue_deletion_job,
    )
    from tests.test_workspace_deletion_execution import run_worker as run_deletion

    created: list[int] = []

    def create(*, member: dict[str, Any] | None = None, delete: bool = True) -> PersonalSeason:
        graph = create_deletion_graph(seasons=1)
        created.append(graph.workspace_id)
        target = PersonalSeason(graph.league_id, graph.season_ids[0], graph.provider_league_id)
        personal.leagues.append(graph.league_id)
        target.graph = graph  # type: ignore[attr-defined]

        if member is not None:
            target.member = personal.member(target, **member)  # type: ignore[attr-defined]

        if delete:
            enqueue_deletion_job(graph)
            assert run_deletion(graph.scheduled_for).succeeded

        return target

    yield create
    personal.cleanup()
    cleanup_graphs(created)


def assert_workspace_authority_gone(target: PersonalSeason) -> None:
    graph = target.graph  # type: ignore[attr-defined]

    with SessionFactory() as session:
        league = session.get(League, target.league_id)
        assert league is not None and league.installation_id is None
        assert session.get(Installation, graph.workspace_id) is None
        assert session.get(ProviderConnection, graph.connection_id) is None
        assert session.get(Season, target.season_id).provider_connection_id is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProviderLeagueClaim)
                .where(ProviderLeagueClaim.id == graph.claim_id)
            )
            == 0
        )


def test_deleted_workspace_fallback_cannot_be_selected(
    workspace_season: Callable[..., PersonalSeason],
) -> None:
    target = workspace_season()
    assert_workspace_authority_gone(target)
    before = team_ids(target.season_id)
    blocked = adapter(target, "blocked")
    recording = RecordingProvider(blocked)

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(target, recording)

    assert raised.value.safe_error_code == "provider_reverification_required"
    assert recording.credentials == [] and blocked.calls == []
    assert team_ids(target.season_id) == before
    season = season_row(target.season_id)
    assert season.sync_state in {SeasonSyncState.STALE, SeasonSyncState.FAILED}
    assert season.last_sync_failure_code == "provider_reverification_required"
    assert_workspace_authority_gone(target)  # nothing was revived


def test_personal_connection_synchronizes_after_workspace_deletion(
    workspace_season: Callable[..., PersonalSeason],
) -> None:
    target = workspace_season(member={"validated_at": datetime.now(UTC)})
    member: Member = target.member  # type: ignore[attr-defined]
    assert_workspace_authority_gone(target)
    recording = RecordingProvider(adapter(target, "after-deletion"))

    result = sync(target, recording)

    assert recording.credentials[0]["swid"] == member.swid
    assert str(target.graph.provider_league_id) == recording.credentials[0]["league_id"]  # type: ignore[attr-defined]
    for private in (member.email, member.display_name, member.swid, member.espn_s2):
        assert private not in repr(result)
    # No SyncRun is linked to a (deleted) workspace connection.
    assert all(run.provider_connection_id is None for run in sync_runs(target.season_id))


def test_public_season_synchronizes_anonymously_after_workspace_deletion(
    workspace_season: Callable[..., PersonalSeason],
) -> None:
    target = workspace_season(member={"proof": UserSeasonProofMode.PUBLIC})
    assert_workspace_authority_gone(target)
    recording = RecordingProvider(adapter(target, "public"))

    sync(target, recording)

    assert set(recording.credentials[0]) == {"league_id", "season"}
    with SessionFactory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserLeagueSeasonAccess)
                .where(UserLeagueSeasonAccess.season_id == target.season_id)
            )
            == 1
        )


def test_private_season_without_eligible_connection_is_stale_after_workspace_deletion(
    workspace_season: Callable[..., PersonalSeason],
) -> None:
    target = workspace_season(member={"connection_active": False})
    assert_workspace_authority_gone(target)
    before = team_ids(target.season_id)
    blocked = adapter(target, "blocked")
    recording = RecordingProvider(blocked)

    with pytest.raises(BackgroundJobExecutionError) as raised:
        sync(target, recording)

    assert raised.value.safe_error_code == "provider_reverification_required"
    assert recording.credentials == [] and blocked.calls == []
    assert team_ids(target.season_id) == before
    season = season_row(target.season_id)
    assert season.sync_state in {SeasonSyncState.STALE, SeasonSyncState.FAILED}
    assert season.last_sync_failure_code == "provider_reverification_required"
    with SessionFactory() as session:
        assert session.get(League, target.league_id).orphaned_at is None  # access still exists
