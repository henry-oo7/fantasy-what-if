"""Critical transaction boundaries for credential replacement and verification.

Real PostgreSQL (``fantasy_what_if_test``) only, separate connections per
actor, deterministic ordering through provider hooks and thread joins with
timeouts. No sleeps and no network. Instrumentation lives in
``tests/support/provider_sync_fixtures.py``.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from app.db.models import League, Season, SyncStatus
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation, ProviderConnection, ProviderLeagueClaim
from app.db.session import SessionFactory
from app.providers.credential_keyring import ProviderCredentialKeyring
from app.providers.credential_vault import ProviderCredentialVault
from app.providers.domain import ProviderCredentials
from app.services.provider_credential_replacement import (
    CHANGED_DURING_VALIDATION_MESSAGE,
    ProviderCredentialReplacementUnavailableError,
    ProviderCredentialReplacementValidationError,
    replace_provider_credentials,
)
from app.services.provider_sync import synchronize_provider
from tests.support.provider_sync_fixtures import (
    KEY,
    WAIT_SECONDS,
    Harness,
    SyncGraph,
    SyntheticSeasonAdapter,
    encrypt_synthetic_credentials,
    forbid_live_network,
    graph_factory,
    harness_context,
    latest_run,
    registry_for,
    run_in_thread,
    worker_settings,
)

NEW_SWID = "{SYNTH-REPLACEMENT-SWID}"
NEW_S2 = "SYNTH-REPLACEMENT-ESPN-S2"


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


def keyring() -> ProviderCredentialKeyring:
    return ProviderCredentialKeyring(keys={1: KEY}, current_version=1)


def submitted(swid: str = NEW_SWID, espn_s2: str = NEW_S2) -> ProviderCredentials:
    return ProviderCredentials(values={"swid": swid, "espn_s2": espn_s2})


def stored(graph: SyncGraph) -> tuple[str | None, int | None, datetime | None]:
    with SessionFactory() as session:
        connection = session.get(ProviderConnection, graph.connection_id)
        assert connection is not None

        return (
            connection.encrypted_credentials,
            connection.credential_version,
            connection.last_validated_at,
        )


def stored_values(graph: SyncGraph) -> dict[str, str]:
    ciphertext, version, _ = stored(graph)
    assert ciphertext is not None and version is not None
    envelope = ProviderCredentialVault(keys={1: KEY}).decrypt(
        encrypted_credentials=ciphertext,
        key_version=version,
        expected_provider=FantasyProvider.ESPN,
        expected_schema_version=1,
    )

    return dict(envelope.credentials.values)


def replace(
    factory: Any,
    graph: SyncGraph,
    adapter: SyntheticSeasonAdapter,
    credentials: ProviderCredentials | None = None,
):
    return replace_provider_credentials(
        factory,
        workspace_id=graph.workspace_id,
        submitted_credentials=credentials or submitted(),
        credential_keyring=keyring(),
        registry=registry_for(adapter),
        now=datetime(2026, 11, 25, tzinfo=UTC),
    )


def adapter_for(
    graph: SyncGraph,
    *,
    before_call: Callable[[str], None] | None = None,
    fail_on: str | None = None,
) -> SyntheticSeasonAdapter:
    return SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id,
        dataset="alpha",
        before_call=before_call,
        fail_on=fail_on,
    )


def _set_workspace(graph: SyncGraph, **values: Any) -> None:
    with SessionFactory() as session:
        session.execute(
            update(Installation).where(Installation.id == graph.workspace_id).values(**values)
        )
        session.commit()


# --------------------------------------------------------------------------
# 1-3: validation holds no transaction, connection or lock
# --------------------------------------------------------------------------


def test_replacement_validates_with_no_session_transaction_or_lock(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    observed: list[str] = []

    def observe(call: str) -> None:
        harness.assert_no_database_state(graph.season_id)
        harness.recorder.log(f"provider:{call}")
        observed.append(call)

    result = replace(harness.factory, graph, adapter_for(graph, before_call=observe))

    assert observed == ["validate_configuration", "fetch_league"]
    recorder = harness.recorder
    last_provider = recorder.index("provider:fetch_league")
    first_provider = recorder.index("provider:validate_configuration")

    # Row locks are taken only by the persistence transaction.
    assert "workspace_lock" not in recorder.kinds()[:first_provider]
    assert recorder.index("workspace_lock") > last_provider

    # The persistence Session is distinct from the preparation Session.
    persistence_session = next(
        ident for kind, ident in recorder.events[last_provider:] if kind == "begin"
    )
    assert persistence_session not in {
        ident for kind, ident in recorder.events[:first_provider] if kind == "session_open"
    }

    values = stored_values(graph)
    assert values["swid"] == NEW_SWID and values["espn_s2"] == NEW_S2
    assert result.connection_id == graph.connection_id


# --------------------------------------------------------------------------
# 4, 5: provider and persistence failure keep the old credentials
# --------------------------------------------------------------------------


def test_provider_failure_keeps_existing_credentials(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = stored(graph)

    with pytest.raises(ProviderCredentialReplacementValidationError):
        replace(harness.factory, graph, adapter_for(graph, fail_on="fetch_league"))

    assert "workspace_lock" not in harness.recorder.kinds()
    assert stored(graph) == before


def test_persistence_failure_keeps_existing_credentials(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = make_graph()
    before = stored(graph)
    # Violates ck_provider_connections_credential_schema_version_positive at
    # flush, after every column of the replacement was assigned.
    monkeypatch.setattr("app.services.provider_credential_replacement.CREDENTIAL_SCHEMA_VERSION", 0)

    with pytest.raises(Exception, match="credential_schema_version"):
        replace(harness.factory, graph, adapter_for(graph))

    assert stored(graph) == before


# --------------------------------------------------------------------------
# 6, 7: lifecycle change during validation prevents replacement
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lifecycle",
    ["deletion_pending", "suspended", "inactive"],
)
def test_lifecycle_change_during_validation_prevents_replacement(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    lifecycle: str,
) -> None:
    graph = make_graph()
    before = stored(graph)

    def change(call: str) -> None:
        if call != "fetch_league":
            return

        if lifecycle == "deletion_pending":
            requested_at = datetime.now(UTC)
            _set_workspace(
                graph,
                registration_status=WorkspaceRegistrationStatus.DELETION_PENDING,
                deletion_requested_at=requested_at,
                deletion_scheduled_for=requested_at + timedelta(days=30),
            )
        elif lifecycle == "suspended":
            _set_workspace(graph, registration_status=WorkspaceRegistrationStatus.SUSPENDED)
        else:
            _set_workspace(graph, status=InstallationStatus.SETUP_IN_PROGRESS)

    with pytest.raises(ProviderCredentialReplacementUnavailableError):
        replace(harness.factory, graph, adapter_for(graph, before_call=change))

    assert stored(graph) == before


# --------------------------------------------------------------------------
# 8: a concurrent replacement wins; the stale one cannot overwrite it
# --------------------------------------------------------------------------


def test_concurrent_replacement_wins_and_stale_replacement_cannot_overwrite(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    winner: list[object] = []

    def concurrent_replacement(call: str) -> None:
        if call == "fetch_league" and not winner:
            winner.append(
                replace(
                    SessionFactory,
                    graph,
                    adapter_for(graph),
                    submitted("{SYNTH-WINNER-SWID}", "SYNTH-WINNER-S2"),
                )
            )

    with pytest.raises(ProviderCredentialReplacementUnavailableError) as raised:
        replace(
            harness.factory,
            graph,
            adapter_for(graph, before_call=concurrent_replacement),
            submitted("{SYNTH-STALE-SWID}", "SYNTH-STALE-S2"),
        )

    assert str(raised.value) == CHANGED_DURING_VALIDATION_MESSAGE
    assert len(winner) == 1
    values = stored_values(graph)
    assert values["swid"] == "{SYNTH-WINNER-SWID}"
    assert values["espn_s2"] == "SYNTH-WINNER-S2"


# --------------------------------------------------------------------------
# 9: provider target, claim or connection change prevents replacement
# --------------------------------------------------------------------------


def _change_league_target(graph: SyncGraph) -> None:
    new_id = f"{graph.provider_league_id}-moved"

    with SessionFactory() as session:
        session.execute(
            update(League).where(League.id == graph.league_id).values(provider_league_id=new_id)
        )
        session.execute(
            update(ProviderLeagueClaim)
            .where(ProviderLeagueClaim.workspace_id == graph.workspace_id)
            .values(provider_league_id=new_id, normalized_provider_league_id=new_id)
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
    "change",
    [_change_league_target, _replace_connection_row],
    ids=["league-and-claim-target", "connection-row"],
)
def test_target_change_during_validation_prevents_replacement(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    change: Callable[[SyncGraph], None],
) -> None:
    graph = make_graph()
    before = stored(graph)

    def change_during_validation(call: str) -> None:
        if call == "fetch_league":
            change(graph)

    with pytest.raises(ProviderCredentialReplacementUnavailableError):
        replace(harness.factory, graph, adapter_for(graph, before_call=change_during_validation))

    assert stored(graph) == before


# --------------------------------------------------------------------------
# 10: synchronization persistence is not blocked by replacement validation
# --------------------------------------------------------------------------


def test_sync_persistence_completes_while_replacement_validates(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    sync_outcome: list[object] = []

    def synchronize_during_validation(call: str) -> None:
        if call != "fetch_league" or sync_outcome:
            return

        # Under the old code the replacement held FOR UPDATE on the
        # workspace here, so this synchronization would block on its
        # persistence lock until the timeout below.
        thread, outcome = run_in_thread(
            lambda: synchronize_provider(
                session_factory=SessionFactory,
                season_id=graph.season_id,
                provider=SyntheticSeasonAdapter(
                    provider_league_id=graph.provider_league_id, dataset="synced"
                ),
                full_sync=True,
            )
        )
        thread.join(WAIT_SECONDS)
        assert not thread.is_alive(), "Synchronization was blocked by credential replacement."
        sync_outcome.extend(outcome)

    replace(harness.factory, graph, adapter_for(graph, before_call=synchronize_during_validation))

    [result] = sync_outcome
    assert not isinstance(result, BaseException), result
    assert latest_run(graph.season_id).status is SyncStatus.SUCCEEDED
    assert stored_values(graph)["swid"] == NEW_SWID


# --------------------------------------------------------------------------
# 11: verify-provider script calls ESPN with no transaction or connection
# --------------------------------------------------------------------------


def _run_verify(
    monkeypatch: pytest.MonkeyPatch,
    harness: Harness,
    graph: SyncGraph,
    adapter: SyntheticSeasonAdapter,
) -> None:
    from app.scripts import verify_provider

    monkeypatch.setattr(sys, "argv", ["verify_provider", "--workspace-id", str(graph.workspace_id)])
    monkeypatch.setattr(verify_provider, "get_settings", worker_settings)
    monkeypatch.setattr(verify_provider, "SessionFactory", harness.factory)
    monkeypatch.setattr(
        "app.services.provider_adapter_resolver.build_provider_registry",
        lambda: registry_for(adapter),
    )
    verify_provider.main()


def test_verify_provider_calls_espn_with_no_transaction_or_connection(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = make_graph()
    before = stored(graph)
    observed: list[str] = []

    def observe(call: str) -> None:
        harness.assert_no_database_state(graph.season_id)
        observed.append(call)

    _run_verify(monkeypatch, harness, graph, adapter_for(graph, before_call=observe))

    assert observed == ["validate_configuration", "fetch_league"]
    output = capsys.readouterr().out
    assert "Active provider verification succeeded." in output
    assert "No database rows were written." in output
    assert stored(graph) == before
    for marker in (graph.swid, graph.espn_s2, before[0] or "<none>", KEY):
        assert marker not in output
    assert f"Connection ID: {graph.connection_id}" not in output


def test_verify_provider_failure_exits_one_without_credentials(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = make_graph()

    with pytest.raises(SystemExit) as raised:
        _run_verify(monkeypatch, harness, graph, adapter_for(graph, fail_on="fetch_league"))

    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "=== VERIFICATION FAILED ===" in output
    assert "Credential values were not displayed." in output
    for marker in (graph.swid, graph.espn_s2, KEY):
        assert marker not in output


# --------------------------------------------------------------------------
# 12: no credential or provider detail leaks from replacement
# --------------------------------------------------------------------------


def test_replacement_never_leaks_credentials(
    harness: Harness,
    make_graph: Callable[[], SyncGraph],
    caplog: pytest.LogCaptureFixture,
) -> None:
    graph = make_graph()
    captured: list[str] = []

    def stale(call: str) -> None:
        if call == "fetch_league":
            _change_league_target(graph)

    with caplog.at_level(logging.DEBUG):
        result = replace(harness.factory, graph, adapter_for(graph))
        captured.append(repr(result))

        for adapter in (
            adapter_for(graph, fail_on="fetch_league"),
            adapter_for(graph, before_call=stale),
        ):
            with pytest.raises(Exception) as raised:
                replace(harness.factory, graph, adapter, submitted("{SYNTH-LEAK-SWID}", "LEAK-S2"))
            captured.extend((str(raised.value), repr(raised.value)))

    captured.append(caplog.text)
    haystack = "\n".join(captured)

    for marker in (
        NEW_SWID,
        NEW_S2,
        "{SYNTH-LEAK-SWID}",
        "LEAK-S2",
        graph.swid,
        graph.espn_s2,
        KEY,
        stored(graph)[0] or "<none>",
    ):
        assert marker not in haystack
