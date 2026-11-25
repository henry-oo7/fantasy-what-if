"""Critical provider-synchronization transaction boundaries and races.

Real PostgreSQL (``fantasy_what_if_test``) only. Every actor uses its own
Session and PostgreSQL connection; races are ordered with threading events
and PostgreSQL lock observation, never with sleeps. Provider adapters are
synthetic and in-process; no test performs network I/O. See
``tests/support/provider_sync_fixtures.py`` for the instrumentation.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.db.models import Season, SyncStatus, Team
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation, ProviderConnection
from app.db.session import SessionFactory
from app.db.session import engine as default_engine
from app.providers.credential_keyring import ProviderCredentialKeyring
from app.providers.errors import ProviderUnavailableError
from app.repositories.background_job_repository import BackgroundJobRepository
from app.services.background_job_handlers import build_background_job_handlers
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    execute_one_background_job,
)
from app.services.provider_sync import (
    SUPERSEDED_SYNC_MESSAGE,
    execute_provider_sync,
    prepare_provider_sync,
    synchronize_provider,
)
from app.services.season_import import SeasonImportUnavailableError, import_provider_season
from app.services.sync_lock import PROVIDER_SYNC_LOCK_NAMESPACE
from tests.support.provider_sync_fixtures import (
    KEY,
    LEASE,
    RAW_RESPONSE_MARKER,
    WAIT_SECONDS,
    WORKER,
    Harness,
    SyncGraph,
    SyntheticSeasonAdapter,
    canonical_state,
    claim,
    encrypt_synthetic_credentials,
    enqueue_sync_job,
    forbid_live_network,
    graph_factory,
    harness_context,
    latest_run,
    registry_for,
    run_in_thread,
    sync_runs,
    worker_settings,
)


@pytest.fixture
def harness() -> Iterator[Harness]:
    with harness_context() as created:
        yield created


@pytest.fixture
def make_graph() -> Iterator[Callable[[], SyncGraph]]:
    with graph_factory() as create:
        yield create


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


# --------------------------------------------------------------------------


def test_worker_provider_io_runs_with_no_session_transaction_connection_or_lock(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    bystander = make_graph()
    bystander_before = canonical_state(bystander)
    observed: list[str] = []

    def observe(call: str) -> None:
        harness.assert_no_database_state(graph.season_id)
        harness.recorder.log(f"provider:{call}")
        observed.append(call)

    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id, dataset="alpha", before_call=observe
    )
    job_id = enqueue_sync_job(graph)

    result = execute_one_background_job(
        session_factory=harness.factory,
        worker_id=WORKER,
        lease_duration=LEASE,
        heartbeat_interval=timedelta(minutes=4),
        handlers=build_background_job_handlers(
            session_factory=harness.factory,
            settings=worker_settings(),
            registry=registry_for(adapter),
        ),
        jitter_ratio=0,
    )

    assert result.job_id == job_id and result.succeeded is True
    assert observed == ["fetch_league", "fetch_matchups", "fetch_weekly_rosters"]

    recorder = harness.recorder
    first_provider = recorder.index("provider:fetch_league")
    last_provider = recorder.index("provider:fetch_weekly_rosters")
    advisory = recorder.index("advisory_lock")
    kinds = recorder.kinds()

    # The advisory and workspace locks belong only to the persistence
    # transaction, strictly after every provider call.
    assert recorder.kinds().count("advisory_lock") == 1
    assert advisory > last_provider
    assert all(kind != "workspace_lock" for kind in kinds[first_provider:last_provider])

    # The persistence transaction runs in a Session distinct from every
    # Session that existed before the provider calls.
    persistence_session = next(
        ident for kind, ident in recorder.events[last_provider:] if kind == "begin"
    )
    pre_provider_sessions = {
        ident for kind, ident in recorder.events[:first_provider] if kind == "session_open"
    }
    assert persistence_session not in pre_provider_sessions

    state = canonical_state(graph)
    assert [team for team in state["teams"] if team.startswith("alpha-")] == [
        f"alpha-{index}" for index in range(1, 5)
    ]
    assert state["league_name"] == "Synthetic alpha League"
    assert latest_run(graph.season_id).status is SyncStatus.SUCCEEDED
    assert canonical_state(bystander) == bystander_before

    with SessionFactory() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None and job.status is BackgroundJobStatus.SUCCEEDED


# --------------------------------------------------------------------------
# 3, 10: provider failure writes nothing and never enters persistence
# --------------------------------------------------------------------------


def test_provider_failure_changes_no_canonical_data_and_records_failed_run(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = canonical_state(graph)
    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id, dataset="alpha", fail_on="fetch_matchups"
    )

    with pytest.raises(ProviderUnavailableError):
        synchronize_provider(
            session_factory=harness.factory,
            season_id=graph.season_id,
            provider=adapter,
            full_sync=True,
        )

    assert "advisory_lock" not in harness.recorder.kinds()
    assert "workspace_lock" not in harness.recorder.kinds()
    assert canonical_state(graph) == before

    run = latest_run(graph.season_id)
    assert run.status is SyncStatus.FAILED
    assert run.completed_at is not None
    assert run.safe_error_message == ProviderUnavailableError.default_safe_message
    assert RAW_RESPONSE_MARKER not in (run.safe_error_message or "")


# --------------------------------------------------------------------------
# 4, 10: persistence failure rolls back every canonical and derived write
# --------------------------------------------------------------------------


def test_persistence_failure_rolls_back_every_write(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = make_graph()
    before = canonical_state(graph)
    reached: list[str] = []

    def fail_after_canonical_writes(session: Session, **kwargs: Any) -> None:
        # Teams, matchups and player scores are already flushed here.
        flushed = session.scalar(
            select(func.count())
            .select_from(Team)
            .where(Team.season_id == graph.season_id, Team.provider_team_id.like("alpha-%"))
        )
        reached.append(f"flushed:{flushed}")
        raise RuntimeError("synthetic derived-analytics failure")

    monkeypatch.setattr(
        "app.services.provider_sync.replace_optimal_lineup_results",
        fail_after_canonical_writes,
    )
    adapter = SyntheticSeasonAdapter(provider_league_id=graph.provider_league_id, dataset="alpha")

    with pytest.raises(RuntimeError, match="synthetic derived-analytics failure"):
        synchronize_provider(
            session_factory=harness.factory,
            season_id=graph.season_id,
            provider=adapter,
            full_sync=True,
        )

    assert reached == ["flushed:4"]
    assert canonical_state(graph) == before

    run = latest_run(graph.season_id)
    assert run.status is SyncStatus.FAILED
    assert run.safe_error_message == "Provider synchronization failed."
    assert run.teams_upserted == 0 and run.analytics_rebuilt is False


# --------------------------------------------------------------------------
# 5: workspace deletion request during fetch prevents persistence
# --------------------------------------------------------------------------


def _mark_deletion_pending(session: Session, workspace_id: int) -> None:
    requested_at = datetime.now(UTC)
    session.execute(
        update(Installation)
        .where(Installation.id == workspace_id)
        .values(
            registration_status=WorkspaceRegistrationStatus.DELETION_PENDING,
            deletion_requested_at=requested_at,
            deletion_scheduled_for=requested_at + timedelta(days=30),
        )
    )


def test_deletion_committed_during_fetch_discards_fetched_data(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = canonical_state(graph)

    def request_deletion(call: str) -> None:
        if call == "fetch_matchups":
            with SessionFactory() as session:
                _mark_deletion_pending(session, graph.workspace_id)
                session.commit()

    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id, dataset="alpha", before_call=request_deletion
    )

    with pytest.raises(BackgroundJobExecutionError) as raised:
        synchronize_provider(
            session_factory=harness.factory,
            season_id=graph.season_id,
            provider=adapter,
            full_sync=True,
        )

    assert raised.value.safe_error_code == "workspace_unavailable"
    assert raised.value.retryable is False
    assert "advisory_lock" not in harness.recorder.kinds()
    assert canonical_state(graph) == before
    assert latest_run(graph.season_id).status is SyncStatus.FAILED


def test_deletion_request_holding_workspace_lock_wins_over_persistence(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    """The request locks the workspace first; persistence waits, then refuses."""

    graph = make_graph()
    before = canonical_state(graph)
    request_session = SessionFactory()
    committed = threading.Event()

    def start_deletion_request(call: str) -> None:
        if call != "fetch_weekly_rosters":
            return

        # Deletion request: lock the workspace row and mark it pending,
        # but do not commit until persistence is observed waiting.
        request_session.scalar(
            select(Installation).where(Installation.id == graph.workspace_id).with_for_update()
        )
        _mark_deletion_pending(request_session, graph.workspace_id)
        request_session.flush()

        def commit_when_persistence_blocks() -> None:
            harness.wait_until_blocked()
            request_session.commit()
            committed.set()

        threading.Thread(target=commit_when_persistence_blocks, daemon=True).start()

    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id,
        dataset="alpha",
        before_call=start_deletion_request,
    )

    try:
        with pytest.raises(BackgroundJobExecutionError) as raised:
            synchronize_provider(
                session_factory=harness.factory,
                season_id=graph.season_id,
                provider=adapter,
                full_sync=True,
            )
    finally:
        assert committed.wait(WAIT_SECONDS)
        request_session.close()

    assert raised.value.safe_error_code == "workspace_unavailable"
    # Persistence never reached the season advisory lock or any write.
    assert "advisory_lock" not in harness.recorder.kinds()
    assert canonical_state(graph) == before


# --------------------------------------------------------------------------
# 6: connection replacement during fetch prevents mismatched persistence
# --------------------------------------------------------------------------


def _replace_credentials_in_place(graph: SyncGraph) -> None:
    with SessionFactory() as session:
        connection = session.get(ProviderConnection, graph.connection_id)
        assert connection is not None
        connection.encrypted_credentials = encrypt_synthetic_credentials(
            graph.provider_league_id, swid="{SYNTH-REPLACED-SWID}", espn_s2="SYNTH-REPLACED-S2"
        )
        session.commit()


def _replace_connection_row(graph: SyncGraph) -> None:
    with SessionFactory() as session:
        old = session.get(ProviderConnection, graph.connection_id)
        assert old is not None
        old.is_active = False
        session.flush()
        replacement = ProviderConnection(
            installation_id=graph.workspace_id,
            provider=FantasyProvider.ESPN,
            display_name="Replacement connection",
            encrypted_credentials=encrypt_synthetic_credentials(
                graph.provider_league_id, swid="{SYNTH-NEW-SWID}", espn_s2="SYNTH-NEW-S2"
            ),
            credential_version=1,
            is_active=True,
        )
        session.add(replacement)
        session.flush()
        session.execute(
            update(Season)
            .where(Season.id == graph.season_id)
            .values(provider_connection_id=replacement.id)
        )
        session.commit()


@pytest.mark.parametrize(
    "replace",
    [_replace_credentials_in_place, _replace_connection_row],
    ids=["credentials-replaced", "connection-row-replaced"],
)
def test_connection_replacement_during_fetch_prevents_persistence(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    replace: Callable[[SyncGraph], None],
) -> None:
    graph = make_graph()
    before = canonical_state(graph)

    def replace_during_fetch(call: str) -> None:
        if call == "fetch_matchups":
            replace(graph)

    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id,
        dataset="alpha",
        before_call=replace_during_fetch,
    )

    with pytest.raises(BackgroundJobExecutionError) as raised:
        synchronize_provider(
            session_factory=harness.factory,
            season_id=graph.season_id,
            provider=adapter,
            full_sync=True,
        )

    assert raised.value.safe_error_code == "provider_connection_changed"
    assert raised.value.retryable is True
    assert canonical_state(graph) == before
    assert latest_run(graph.season_id).status is SyncStatus.FAILED


# --------------------------------------------------------------------------
# 7: out-of-order completion cannot let older data overwrite newer data
# --------------------------------------------------------------------------


def test_older_fetch_finishing_last_cannot_overwrite_newer_data(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    """A prepares first; B prepares later, fetches and commits while A is
    still fetching; A then reaches persistence and must be discarded."""

    graph = make_graph()
    older_fetching = threading.Event()
    release_older = threading.Event()

    def hold_older(call: str) -> None:
        if call == "fetch_league":
            older_fetching.set()
            assert release_older.wait(WAIT_SECONDS)

    older = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id, dataset="older", before_call=hold_older
    )
    newer = SyntheticSeasonAdapter(provider_league_id=graph.provider_league_id, dataset="newer")

    thread, older_outcome = run_in_thread(
        lambda: synchronize_provider(
            session_factory=harness.factory,
            season_id=graph.season_id,
            provider=older,
            full_sync=True,
        )
    )
    assert older_fetching.wait(WAIT_SECONDS)

    # Both attempts are now fetching concurrently; the newer one commits.
    newer_result = synchronize_provider(
        session_factory=harness.factory,
        season_id=graph.season_id,
        provider=newer,
        full_sync=True,
    )
    after_newer = canonical_state(graph)

    release_older.set()
    thread.join(WAIT_SECONDS)
    assert not thread.is_alive()

    [older_result] = older_outcome
    assert not isinstance(older_result, BaseException), older_result
    assert newer_result.superseded is False
    assert older_result.superseded is True
    assert older_result.teams_upserted == 0

    final = canonical_state(graph)
    assert final == after_newer
    assert final["league_name"] == "Synthetic newer League"
    assert not any(team.startswith("older-") for team in final["teams"])
    assert not any(player.startswith("older-") for player in final["player_week_scores"])

    older_run, newer_run = sync_runs(graph.season_id)[-2:]
    assert older_run.id < newer_run.id
    assert newer_run.status is SyncStatus.SUCCEEDED
    assert older_run.status is SyncStatus.FAILED
    assert older_run.safe_error_message == SUPERSEDED_SYNC_MESSAGE


def test_newer_fetch_finishing_last_still_persists(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()

    with harness.factory() as session:
        older_plan = prepare_provider_sync(
            session, season_id=graph.season_id, provider=_adapter(graph, "older"), full_sync=True
        )
        session.commit()

    with harness.factory() as session:
        newer_plan = prepare_provider_sync(
            session, season_id=graph.season_id, provider=_adapter(graph, "newer"), full_sync=True
        )
        session.commit()

    older = execute_provider_sync(
        session_factory=harness.factory, plan=older_plan, provider=_adapter(graph, "older")
    )
    newer = execute_provider_sync(
        session_factory=harness.factory, plan=newer_plan, provider=_adapter(graph, "newer")
    )

    assert older.superseded is False and newer.superseded is False
    assert canonical_state(graph)["league_name"] == "Synthetic newer League"
    assert [run.status for run in sync_runs(graph.season_id)[-2:]] == [
        SyncStatus.SUCCEEDED,
        SyncStatus.SUCCEEDED,
    ]


def _adapter(graph: SyncGraph, dataset: str) -> SyntheticSeasonAdapter:
    return SyntheticSeasonAdapter(provider_league_id=graph.provider_league_id, dataset=dataset)


def test_season_advisory_lock_still_serializes_persistence(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    """A holder of the season lock forces a retryable refusal with no writes."""

    graph = make_graph()
    before = canonical_state(graph)

    with default_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :season_id)"),
            {"namespace": PROVIDER_SYNC_LOCK_NAMESPACE, "season_id": graph.season_id},
        )

        with pytest.raises(BackgroundJobExecutionError) as raised:
            synchronize_provider(
                session_factory=harness.factory,
                season_id=graph.season_id,
                provider=_adapter(graph, "alpha"),
                full_sync=True,
            )

        transaction.rollback()

    assert raised.value.safe_error_code == "sync_already_running"
    assert raised.value.retryable is True
    assert canonical_state(graph) == before
    assert latest_run(graph.season_id).status is SyncStatus.FAILED


# --------------------------------------------------------------------------
# 8: lost job authority cannot commit
# --------------------------------------------------------------------------


def test_lost_job_lease_during_fetch_cannot_commit(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = canonical_state(graph)
    job_id = enqueue_sync_job(graph)
    snapshot = claim(job_id)

    def recover_lease(call: str) -> None:
        if call != "fetch_matchups":
            return

        # The lease expires and lease recovery reschedules the job.
        with SessionFactory() as session:
            job = session.get(BackgroundJob, job_id, with_for_update=True)
            assert job is not None
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.flush()
            BackgroundJobRepository(session).recover_expired_lease(
                job, retry_at=datetime.now(UTC) + timedelta(minutes=5)
            )
            session.commit()

    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id, dataset="alpha", before_call=recover_lease
    )
    handler = build_background_job_handlers(
        session_factory=harness.factory,
        settings=worker_settings(),
        registry=registry_for(adapter),
    )[BackgroundJobType.FULL_SEASON_SYNC]

    with pytest.raises(BackgroundJobExecutionError) as raised:
        handler(snapshot)

    assert raised.value.safe_error_code == "job_authority_lost"
    assert "advisory_lock" not in harness.recorder.kinds()
    assert canonical_state(graph) == before
    assert latest_run(graph.season_id).status is SyncStatus.FAILED

    with SessionFactory() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None and job.status is BackgroundJobStatus.RETRY_SCHEDULED


# --------------------------------------------------------------------------
# 12: no credential, cookie, SWID, raw response or owner identity leaks
# --------------------------------------------------------------------------


def test_credentials_and_provider_detail_never_leak(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    caplog: pytest.LogCaptureFixture,
) -> None:
    graph = make_graph()
    failing = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id, dataset="alpha", fail_on="fetch_league"
    )
    succeeding = _adapter(graph, "alpha")
    failing_job = enqueue_sync_job(graph)
    captured: list[str] = []

    with caplog.at_level(logging.DEBUG):
        for adapter in (failing, succeeding):
            handler = build_background_job_handlers(
                session_factory=harness.factory,
                settings=worker_settings(),
                registry=registry_for(adapter),
            )[BackgroundJobType.FULL_SEASON_SYNC]
            snapshot = claim(failing_job) if adapter is failing else None

            if snapshot is not None:
                with pytest.raises(ProviderUnavailableError) as raised:
                    handler(snapshot)
                captured.extend((str(raised.value.__class__), repr(snapshot)))
            else:
                with harness.factory() as session:
                    plan = prepare_provider_sync(
                        session, season_id=graph.season_id, provider=adapter, full_sync=True
                    )
                    session.commit()
                captured.append(repr(plan))
                execute_provider_sync(session_factory=harness.factory, plan=plan, provider=adapter)

    with SessionFactory() as session:
        job = session.get(BackgroundJob, failing_job)
        assert job is not None
        captured.extend((str(job.payload), str(job.safe_error_code), str(job.safe_error_message)))

    for run in sync_runs(graph.season_id):
        captured.extend((str(run.provider_request_metadata), str(run.safe_error_message)))

    captured.append(caplog.text)
    haystack = "\n".join(captured)

    with SessionFactory() as session:
        workspace = session.get(Installation, graph.workspace_id)
        assert workspace is not None
        owner_markers = [workspace.slug]

    for marker in (graph.swid, graph.espn_s2, "espn_s2", "swid", RAW_RESPONSE_MARKER, KEY):
        assert marker not in haystack
    for marker in owner_markers:
        assert marker not in haystack


# --------------------------------------------------------------------------
# Season import: provider I/O outside transactions, atomic persistence
# --------------------------------------------------------------------------


def _import_keyring() -> ProviderCredentialKeyring:
    return ProviderCredentialKeyring(keys={1: KEY}, current_version=1)


def _import_state(graph: SyncGraph) -> dict[str, Any]:
    with SessionFactory() as session:
        workspace = session.get(Installation, graph.workspace_id)
        connection = session.get(ProviderConnection, graph.connection_id)
        assert workspace is not None and connection is not None

        return {
            "seasons": sorted(
                session.scalars(select(Season.year).where(Season.league_id == graph.league_id))
            ),
            "active_league_id": workspace.active_league_id,
            "active_season_id": workspace.active_season_id,
            "last_validated_at": connection.last_validated_at,
            "imported_teams": int(
                session.scalar(
                    select(func.count())
                    .select_from(Team)
                    .join(Season, Season.id == Team.season_id)
                    .where(Season.league_id == graph.league_id, Season.year == 2025)
                )
                or 0
            ),
        }


def test_season_import_provider_io_holds_no_database_state(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    observed: list[str] = []

    def observe(call: str) -> None:
        harness.assert_no_database_state(graph.season_id)
        harness.recorder.log(f"provider:{call}")
        observed.append(call)

    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id,
        dataset="imported",
        season_year=2025,
        before_call=observe,
    )

    result = import_provider_season(
        harness.factory,
        workspace_id=graph.workspace_id,
        season_year=2025,
        credential_keyring=_import_keyring(),
        registry=registry_for(adapter),
        now=datetime(2026, 11, 25, tzinfo=UTC),
    )

    assert observed == [
        "validate_configuration",
        "fetch_league",
        "fetch_league",
        "fetch_matchups",
        "fetch_weekly_rosters",
    ]
    recorder = harness.recorder
    last_provider = recorder.index("provider:fetch_weekly_rosters", last=True)
    assert recorder.index("workspace_lock") > last_provider
    assert recorder.index("advisory_lock") > last_provider

    state = _import_state(graph)
    assert state["seasons"] == [2025, 2026]
    assert state["imported_teams"] == 4
    assert result.sync_result.teams_upserted == 4
    assert latest_run(result.season_id).status is SyncStatus.SUCCEEDED


def test_season_import_persistence_failure_creates_nothing(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = make_graph()
    before = _import_state(graph)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic import persistence failure")

    monkeypatch.setattr("app.services.provider_sync.replace_optimal_lineup_results", fail)
    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id, dataset="imported", season_year=2025
    )

    with pytest.raises(RuntimeError, match="synthetic import persistence failure"):
        import_provider_season(
            harness.factory,
            workspace_id=graph.workspace_id,
            season_year=2025,
            credential_keyring=_import_keyring(),
            registry=registry_for(adapter),
            now=datetime(2026, 11, 25, tzinfo=UTC),
        )

    assert _import_state(graph) == before


@pytest.mark.parametrize(
    "change",
    ["deletion_pending", "credentials_replaced"],
)
def test_season_import_discards_fetch_after_state_change(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    change: str,
) -> None:
    graph = make_graph()
    before = _import_state(graph)

    def change_during_fetch(call: str) -> None:
        if call != "fetch_matchups":
            return

        if change == "deletion_pending":
            with SessionFactory() as session:
                _mark_deletion_pending(session, graph.workspace_id)
                session.commit()
        else:
            _replace_credentials_in_place(graph)

    adapter = SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id,
        dataset="imported",
        season_year=2025,
        before_call=change_during_fetch,
    )

    with pytest.raises(SeasonImportUnavailableError):
        import_provider_season(
            harness.factory,
            workspace_id=graph.workspace_id,
            season_year=2025,
            credential_keyring=_import_keyring(),
            registry=registry_for(adapter),
            now=datetime(2026, 11, 25, tzinfo=UTC),
        )

    after = _import_state(graph)
    assert after["seasons"] == before["seasons"]
    assert after["imported_teams"] == 0
    assert after["active_season_id"] == before["active_season_id"]
