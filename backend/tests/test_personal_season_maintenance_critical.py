"""Critical personal Season maintenance behavior (ADR 0010).

Real PostgreSQL (``fantasy_what_if_test``) only. Providers are the in-process
``FakeEspn`` transport (no network); users, connections and credentials are
synthetic. Races are ordered by hooks that run while the provider call is in
flight (no transaction open) and by threading events, never by sleeps.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db.auth_models import User
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import BackgroundJob, ProviderConnection
from app.db.session import SessionFactory
from app.repositories.provider_sync_repository import ProviderSyncRepository
from app.services.access_reverification import (
    CREDENTIALS_REJECTED_CODE,
    ReverificationOutcome,
    enqueue_access_reverification,
    reverify_access,
)
from app.services.background_job_handlers import AccessReverificationJobHandler
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    execute_one_background_job,
)
from app.services.scheduled_synchronization_jobs import (
    ScheduledSynchronizationTarget,
    enqueue_scheduled_synchronization,
)
from app.services.season_maintenance import (
    enqueue_maintenance,
    list_due_reverifications,
)
from tests.support.league_import import FakeEspn, FakeResponse, unique_league_id
from tests.support.personal_maintenance import (
    Member,
    Target,
    World,
    access_row,
    connection_row,
    jobs_for,
    scope_discovery,
    update_season,
)
from tests.support.provider_sync_fixtures import (
    WAIT_SECONDS,
    WORKER,
    Harness,
    SyncGraph,
    encrypt_synthetic_credentials,
    forbid_live_network,
    graph_factory,
    harness_context,
    worker_settings,
)
from tests.support.workspace_deletion_graph import TEST_JOB_PRIORITY
from tests.test_canonical_workspace_compatibility_critical import (
    delete_workspace,
    graphs,  # noqa: F401  (fixture)
)

SECRET_MARKER = "SECRET-PROVIDER-DETAIL"
PRIVATE = UserSeasonProofMode.PRIVATE_CREDENTIALS
PUBLIC = UserSeasonProofMode.PUBLIC
ACTIVE = UserSeasonAccessStatus.ACTIVE
FLAGGED = UserSeasonAccessStatus.REVERIFICATION_REQUIRED


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    created = World()
    scope_discovery(monkeypatch, created)
    yield created
    created.cleanup()


@pytest.fixture
def harness() -> Iterator[Harness]:
    with harness_context() as created:
        yield created


def reverify(
    member: Member,
    target: Target,
    espn: Callable[..., FakeResponse],
    *,
    factory=SessionFactory,
) -> ReverificationOutcome:
    return reverify_access(
        session_factory=factory,
        user_id=member.user_id,
        season_id=target.season_id,
        settings=worker_settings(),
        http_get=espn,
    )


def private_espn(*members: Member) -> FakeEspn:
    return FakeEspn(mode="private", valid_s2={member.espn_s2 for member in members})


def active_jobs(key: str) -> int:
    return sum(
        job.status
        in (
            BackgroundJobStatus.PENDING,
            BackgroundJobStatus.RUNNING,
            BackgroundJobStatus.RETRY_SCHEDULED,
        )
        for job in jobs_for(key)
    )


# --------------------------------------------------------------------------
# Scheduler: one Season job, independent of workspaces
# --------------------------------------------------------------------------


def test_two_scheduler_passes_and_two_users_enqueue_one_season_job(world: World) -> None:
    target = world.season()
    world.member(target, proof=PUBLIC, verified_ago=timedelta(days=1))
    world.member(target, proof=PUBLIC, verified_ago=timedelta(days=1))
    key = f"league-season-sync:{target.season_id}"
    now = datetime.now(UTC)
    barrier = threading.Barrier(2)

    def pass_() -> None:
        with SessionFactory() as session:
            barrier.wait(WAIT_SECONDS)
            enqueue_maintenance(session, now=now)
            session.commit()

    threads = [threading.Thread(target=pass_) for _ in range(2)]
    [thread.start() for thread in threads]
    [thread.join(WAIT_SECONDS) for thread in threads]

    assert active_jobs(key) == 1
    with SessionFactory() as session:
        assert enqueue_maintenance(session, now=now).sync_created == 0
        session.commit()
    assert active_jobs(key) == 1


def test_workspace_and_personal_scheduling_share_one_active_season_job(
    world: World,
) -> None:
    with graph_factory() as create:
        graph: SyncGraph = create()
        target = Target(graph.league_id, graph.season_id, graph.provider_league_id)
        world.member(target, proof=PUBLIC, verified_ago=timedelta(days=1))
        update_season(graph.season_id, last_successful_sync_at=None, last_sync_attempt_at=None)
        key = f"league-season-sync:{graph.season_id}"
        now = datetime.now(UTC)

        try:
            with SessionFactory() as session:
                enqueue_maintenance(session, now=now)
                enqueue_scheduled_synchronization(
                    session,
                    target=ScheduledSynchronizationTarget(graph.workspace_id, graph.season_id),
                    scheduled_for=now,
                )
                session.commit()

            [job] = jobs_for(key)
            # The shared key carries the League's workspace (transitional) and
            # never a user, whichever scheduler created it first.
            assert job.workspace_id == graph.workspace_id
            assert set(job.payload) == {"version", "season_id"}
        finally:
            with SessionFactory() as session:
                session.execute(
                    BackgroundJob.__table__.delete().where(BackgroundJob.idempotency_key == key)
                )
                session.commit()


def test_orphaned_and_accessless_leagues_are_not_scheduled(world: World) -> None:
    orphan = world.season(orphaned_at=datetime.now(UTC))
    world.member(orphan, proof=PUBLIC, status=FLAGGED)
    flagged_only = world.season()
    world.member(flagged_only, proof=PUBLIC, status=FLAGGED, verified_ago=timedelta(days=1))
    world.member(flagged_only, status=UserSeasonAccessStatus.REVOKED)
    empty = world.season()

    with SessionFactory() as session:
        enqueue_maintenance(session, now=datetime.now(UTC))
        session.commit()

    for target in (orphan, flagged_only, empty):
        assert jobs_for(f"league-season-sync:{target.season_id}") == []


# --------------------------------------------------------------------------
# Reverification: independence and no automatic revocation
# --------------------------------------------------------------------------


def test_two_users_are_reverified_independently(world: World) -> None:
    target = world.season()
    good = world.member(target)
    bad = world.member(target)
    espn = private_espn(good)

    assert reverify(good, target, espn) is ReverificationOutcome.PRIVATE
    good_after_own = access_row(good, target.season_id)
    assert reverify(bad, target, espn) is ReverificationOutcome.CREDENTIALS_REJECTED

    good_row, bad_row = access_row(good, target.season_id), access_row(bad, target.season_id)
    assert good_row is not None and bad_row is not None
    assert good_row.status is ACTIVE and good_row.last_reverified_at is not None
    assert (good_row.status, good_row.last_reverified_at, good_row.updated_at) == (
        good_after_own.status,
        good_after_own.last_reverified_at,
        good_after_own.updated_at,
    )
    assert connection_row(good.connection_id).last_validation_error is None
    assert bad_row.status is FLAGGED and bad_row.last_reverified_at is None
    assert bad_row.provider_connection_id == bad.connection_id
    assert connection_row(bad.connection_id).last_validation_error == CREDENTIALS_REJECTED_CODE
    # The rejected user's own credentials, never the other's, were what ESPN saw.
    assert {call["cookies"]["espn_s2"] for call in espn.calls if call["cookies"]} == {
        good.espn_s2,
        bad.espn_s2,
    }


@pytest.mark.parametrize("mode", ["missing", "no_metadata", "wrong_league"])
def test_ambiguous_provider_outcomes_require_reverification_never_revoke(
    world: World, mode: str
) -> None:
    target = world.season()
    member, bystander = world.member(target), world.member(target)
    before = access_row(bystander, target.season_id)

    assert reverify(member, target, FakeEspn(mode=mode)) is ReverificationOutcome.RECONNECT_REQUIRED

    row = access_row(member, target.season_id)
    assert row is not None and row.status is FLAGGED and row.last_reverified_at is None
    # A 404 is ambiguous: the connection is not declared invalid by it.
    assert connection_row(member.connection_id).last_validation_error is None
    after = access_row(bystander, target.season_id)
    assert (after.status, after.updated_at) == (before.status, before.updated_at)


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_provider_outage_changes_nothing_and_retries(world: World, status_code: int) -> None:
    target = world.season()
    member = world.member(target)
    before = access_row(member, target.season_id)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        reverify(member, target, lambda *a, **k: FakeResponse(status_code, {"m": SECRET_MARKER}))

    assert caught.value.retryable is True and SECRET_MARKER not in str(caught.value)
    after = access_row(member, target.season_id)
    assert (after.status, after.updated_at, after.provider_connection_id) == (
        before.status,
        before.updated_at,
        before.provider_connection_id,
    )
    assert connection_row(member.connection_id).last_validation_error is None


def test_timeout_changes_nothing_and_retries(world: World) -> None:
    target = world.season()
    member = world.member(target)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        reverify(member, target, FakeEspn(mode="timeout"))

    assert caught.value.retryable is True and SECRET_MARKER not in str(caught.value)
    assert access_row(member, target.season_id).status is ACTIVE


@pytest.mark.parametrize(
    "mode", ["missing", "no_metadata", "wrong_league", "error500", "timeout", "private"]
)
def test_no_provider_outcome_ever_revokes(world: World, mode: str) -> None:
    target = world.season()
    members = [
        world.member(target, proof=proof, status=status)
        for proof in (PRIVATE, PUBLIC)
        for status in (ACTIVE, FLAGGED)
    ]

    for member in members:
        with contextlib.suppress(BackgroundJobExecutionError):
            reverify(member, target, FakeEspn(mode=mode, valid_s2=set()))

        assert access_row(member, target.season_id).status is not UserSeasonAccessStatus.REVOKED


def test_revoked_access_is_never_reverified(world: World) -> None:
    target = world.season()
    member = world.member(target, status=UserSeasonAccessStatus.REVOKED)
    espn = FakeEspn(mode="public")

    assert reverify(member, target, espn) is ReverificationOutcome.NOT_APPLICABLE
    assert espn.calls == []
    assert access_row(member, target.season_id).status is UserSeasonAccessStatus.REVOKED


def test_reverification_uses_only_the_users_own_credentials(world: World) -> None:
    target = world.season()
    owner, other = world.member(target), world.member(target, with_connection=False, proof=PUBLIC)
    espn = private_espn(owner)  # only the owner's cookies would work

    assert reverify(other, target, espn) is ReverificationOutcome.RECONNECT_REQUIRED
    assert all(call["cookies"] is None for call in espn.calls)  # no one else's credentials
    assert access_row(other, target.season_id).status is FLAGGED
    assert access_row(owner, target.season_id).status is ACTIVE


def test_one_invalid_connection_does_not_block_another(world: World) -> None:
    target = world.season()
    broken = world.member(target, connection_error=CREDENTIALS_REJECTED_CODE)
    working = world.member(target)

    with SessionFactory() as session:
        eligible = ProviderSyncRepository(session).eligible_private_connections(
            season_id=target.season_id, provider=FantasyProvider.ESPN
        )

    assert [connection.id for connection in eligible] == [working.connection_id]
    assert access_row(broken, target.season_id).status is ACTIVE  # never revoked by invalidity


# --------------------------------------------------------------------------
# Public / private transitions
# --------------------------------------------------------------------------


def test_public_to_private_requires_independent_proof_from_each_public_user(
    world: World,
) -> None:
    target = world.season()
    first = world.member(target, proof=PUBLIC, with_connection=False)
    keeps = world.member(target, proof=PUBLIC, with_connection=True)
    gone = world.member(target, proof=PUBLIC, with_connection=False)
    espn = private_espn(keeps)

    assert reverify(first, target, espn) is ReverificationOutcome.RECONNECT_REQUIRED
    assert access_row(first, target.season_id).status is FLAGGED

    # The still-active public-proof peers are queued, not changed, by that proof.
    for peer in (keeps, gone):
        assert access_row(peer, target.season_id).status is ACTIVE
        assert active_jobs(f"access-reverification:{peer.user_id}:{target.season_id}") == 1
    assert active_jobs(f"access-reverification:{first.user_id}:{target.season_id}") == 0

    assert reverify(keeps, target, espn) is ReverificationOutcome.PRIVATE
    assert reverify(gone, target, espn) is ReverificationOutcome.RECONNECT_REQUIRED

    kept = access_row(keeps, target.season_id)
    assert kept.status is ACTIVE and kept.proof_mode is PRIVATE
    assert kept.provider_connection_id == keeps.connection_id  # its own, never another's
    assert access_row(gone, target.season_id).status is FLAGGED
    with SessionFactory() as session:
        assert (
            ProviderSyncRepository(session).has_active_public_access(season_id=target.season_id)
            is False
        )


def test_private_to_public_proof_keeps_connection_and_enables_anonymous_sync(
    world: World,
) -> None:
    target = world.season()
    member = world.member(target)
    before = connection_row(member.connection_id)

    assert reverify(member, target, FakeEspn(mode="public")) is ReverificationOutcome.PUBLIC

    row = access_row(member, target.season_id)
    assert row.status is ACTIVE and row.proof_mode is PUBLIC and row.provider_connection_id is None
    after = connection_row(member.connection_id)
    assert after is not None and after.is_active
    assert (after.encrypted_credentials, after.credential_version) == (
        before.encrypted_credentials,
        before.credential_version,
    )
    with SessionFactory() as session:
        assert ProviderSyncRepository(session).has_active_public_access(season_id=target.season_id)


def test_flagged_access_recovers_when_proof_succeeds_again(world: World) -> None:
    target = world.season()
    member = world.member(target, status=FLAGGED)

    assert reverify(member, target, private_espn(member)) is ReverificationOutcome.PRIVATE
    assert access_row(member, target.season_id).status is ACTIVE


# --------------------------------------------------------------------------
# Races: stale plans never persist
# --------------------------------------------------------------------------


def _change_during_proof(
    world: World, change: Callable[[Member, Target], None], harness: Harness | None = None
):
    target = world.season()
    member = world.member(target)
    other = world.member(target)
    espn = private_espn(member, other)
    espn.before_respond = lambda: change(member, target)
    factory = harness.factory if harness is not None else SessionFactory

    return target, member, other, reverify(member, target, espn, factory=factory)


def test_access_deleted_during_proof_persists_nothing(world: World) -> None:
    def delete_access(member: Member, target: Target) -> None:
        with SessionFactory() as session:
            session.execute(
                UserLeagueSeasonAccess.__table__.delete().where(
                    UserLeagueSeasonAccess.user_id == member.user_id
                )
            )
            session.commit()

    target, member, other, outcome = _change_during_proof(world, delete_access)

    assert outcome is ReverificationOutcome.DISCARDED
    assert access_row(member, target.season_id) is None
    assert connection_row(member.connection_id).last_validated_at < datetime.now(UTC) - timedelta(
        days=7
    )
    assert access_row(other, target.season_id).last_reverified_at is None


def test_connection_replaced_during_proof_persists_nothing(world: World) -> None:
    def replace(member: Member, target: Target) -> None:
        with SessionFactory() as session:
            session.execute(
                update(ProviderConnection)
                .where(ProviderConnection.id == member.connection_id)
                .values(
                    encrypted_credentials=encrypt_synthetic_credentials(
                        target.provider_league_id, swid="{NEW-SWID}", espn_s2="NEW-S2"
                    )
                )
            )
            session.commit()

    target, member, _, outcome = _change_during_proof(world, replace)

    assert outcome is ReverificationOutcome.DISCARDED
    row = access_row(member, target.season_id)
    assert row.status is ACTIVE and row.last_reverified_at is None
    assert connection_row(member.connection_id).last_validation_error is None


def test_user_deleted_during_proof_persists_nothing(world: World) -> None:
    def delete_user(member: Member, target: Target) -> None:
        with SessionFactory() as session:
            session.execute(User.__table__.delete().where(User.id == member.user_id))
            session.commit()

    target, member, other, outcome = _change_during_proof(world, delete_user)

    assert outcome is ReverificationOutcome.DISCARDED
    assert access_row(member, target.season_id) is None
    assert access_row(other, target.season_id).last_reverified_at is None


@pytest.mark.parametrize("change", ["deactivated", "re-proved", "flagged"])
def test_user_or_access_changed_during_proof_discards_result(world: World, change: str) -> None:
    def alter(member: Member, target: Target) -> None:
        with SessionFactory() as session:
            if change == "deactivated":
                session.execute(
                    update(User).where(User.id == member.user_id).values(is_active=False)
                )
            elif change == "re-proved":  # e.g. a concurrent import switched the proof mode
                session.execute(
                    update(UserLeagueSeasonAccess)
                    .where(UserLeagueSeasonAccess.user_id == member.user_id)
                    .values(
                        proof_mode=PUBLIC,
                        provider_connection_id=None,
                        last_reverified_at=datetime.now(UTC),
                    )
                )
            else:
                session.execute(
                    update(UserLeagueSeasonAccess)
                    .where(UserLeagueSeasonAccess.user_id == member.user_id)
                    .values(status=FLAGGED)
                )
            session.commit()

    target, member, _, outcome = _change_during_proof(world, alter)

    assert outcome is ReverificationOutcome.DISCARDED
    assert connection_row(member.connection_id).last_validation_error is None


def test_provider_call_runs_with_no_transaction_or_lock_held(
    world: World, harness: Harness
) -> None:
    observations: list[int] = []
    target = world.season()
    member = world.member(target)
    espn = private_espn(member)
    espn.before_respond = lambda: observations.append(harness.backend_lock_count())

    assert reverify(member, target, espn, factory=harness.factory) is ReverificationOutcome.PRIVATE
    assert observations and set(observations) == {0}


# --------------------------------------------------------------------------
# Durable job: authority, idempotency and no leakage
# --------------------------------------------------------------------------


def _handlers(harness: Harness, espn: FakeEspn):
    return {
        BackgroundJobType.ACCESS_REVERIFICATION: AccessReverificationJobHandler(
            session_factory=harness.factory, settings=worker_settings(), http_get=espn
        )
    }


def _run_worker(harness: Harness, espn: FakeEspn):
    return execute_one_background_job(
        session_factory=harness.factory,
        worker_id=WORKER,
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=4),
        handlers=_handlers(harness, espn),
        jitter_ratio=0,
    )


def _enqueue(member: Member, target: Target) -> int:
    with SessionFactory() as session:
        result = enqueue_access_reverification(
            session, user_id=member.user_id, season_id=target.season_id, priority=TEST_JOB_PRIORITY
        )
        session.commit()
        return result.job.id


def test_reverification_job_is_idempotent_and_exact(world: World) -> None:
    target = world.season()
    member = world.member(target)

    first, second = _enqueue(member, target), _enqueue(member, target)

    assert first == second
    [job] = jobs_for(f"access-reverification:{member.user_id}:{target.season_id}")
    assert job.workspace_id is None
    assert job.payload == {"version": 1, "user_id": member.user_id, "season_id": target.season_id}


def test_worker_runs_reverification_and_job_rows_hold_no_secret(
    world: World, harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    target = world.season()
    member = world.member(target)
    job_id = _enqueue(member, target)
    espn = private_espn(member)

    outcome = _run_worker(harness, espn)

    assert outcome.job_id == job_id and outcome.succeeded is True
    assert access_row(member, target.season_id).last_reverified_at is not None
    [job] = jobs_for(f"access-reverification:{member.user_id}:{target.season_id}")
    assert job.status is BackgroundJobStatus.SUCCEEDED
    text = f"{job.payload}{job.safe_error_message}{job.safe_error_code}{caplog.text}"
    assert member.espn_s2 not in text and member.swid not in text


def test_outage_in_worker_schedules_retry_without_leaking(world: World, harness: Harness) -> None:
    target = world.season()
    member = world.member(target)
    job_id = _enqueue(member, target)

    outcome = _run_worker(harness, FakeEspn(mode="error500"))

    assert outcome.job_id == job_id and outcome.retry_scheduled is True
    [job] = jobs_for(f"access-reverification:{member.user_id}:{target.season_id}")
    assert job.status is BackgroundJobStatus.RETRY_SCHEDULED
    assert SECRET_MARKER not in f"{job.safe_error_message}{job.safe_error_code}"
    assert access_row(member, target.season_id).status is ACTIVE


def test_lease_lost_during_proof_prevents_persistence(world: World, harness: Harness) -> None:
    target = world.season()
    member = world.member(target)
    job_id = _enqueue(member, target)
    espn = private_espn(member)

    def lose_lease() -> None:
        with SessionFactory() as session:
            session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .values(lease_expires_at=func.now() - timedelta(minutes=1))
            )
            session.commit()

    espn.before_respond = lose_lease
    outcome = _run_worker(harness, espn)

    assert outcome.job_id == job_id and outcome.succeeded is False
    row = access_row(member, target.season_id)
    assert row.status is ACTIVE and row.last_reverified_at is None
    assert connection_row(member.connection_id).last_validation_error is None


# --------------------------------------------------------------------------
# Workspace independence
# --------------------------------------------------------------------------


def test_workspace_deletion_preserves_personal_access_and_reverification(graphs) -> None:  # noqa: F811
    graph = graphs(personal_access=True)
    delete_workspace(graph)  # removes the claim and fallback; shared rows are kept
    season_id = graph.season_ids[0]

    with SessionFactory() as session:
        access = session.scalar(
            select(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.season_id == season_id)
        )
        assert access is not None and access.status is ACTIVE
        user_id = access.user_id
        # ESPN IDs are numeric; the compatibility graph's synthetic ID is not.
        session.execute(
            update(League)
            .where(League.id == graph.league_id)
            .values(provider_league_id=unique_league_id())
        )
        access.verified_at = datetime.now(UTC) - timedelta(days=8)
        session.commit()

    with SessionFactory() as session:
        due = list_due_reverifications(session, now=datetime.now(UTC))
        assert (user_id, season_id) in due

    outcome = reverify_access(
        session_factory=SessionFactory,
        user_id=user_id,
        season_id=season_id,
        settings=worker_settings(),
        http_get=FakeEspn(mode="public"),
    )

    assert outcome is ReverificationOutcome.PUBLIC
    with SessionFactory() as session:
        row = session.scalar(
            select(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.user_id == user_id)
        )
        assert row.status is ACTIVE and row.proof_mode is PUBLIC
        assert session.get(League, graph.league_id).orphaned_at is None
