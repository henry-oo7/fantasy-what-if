"""Routine CLI behavior of ``app.scripts.verify_provider``.

Committed rows in ``fantasy_what_if_test`` and a synthetic in-process
adapter. No network and no credential values may appear in output.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import update

from app.db.platform_models import Installation, ProviderConnection
from app.db.session import SessionFactory
from app.providers.domain import ProviderValidationResult
from app.scripts import verify_provider
from tests.support.provider_sync_fixtures import (
    KEY,
    SyncGraph,
    SyntheticSeasonAdapter,
    forbid_live_network,
    graph_factory,
    registry_for,
    worker_settings,
)


class RejectingAdapter(SyntheticSeasonAdapter):
    def validate_configuration(self) -> ProviderValidationResult:
        self.calls.append("validate_configuration")

        return ProviderValidationResult(
            is_valid=False,
            provider=self.provider,
            safe_message="The fantasy provider credentials were rejected.",
        )


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


@pytest.fixture
def make_graph() -> Iterator[Callable[[], SyncGraph]]:
    with graph_factory() as create:
        yield create


def run_cli(
    monkeypatch: pytest.MonkeyPatch,
    workspace_id: int,
    adapter: SyntheticSeasonAdapter,
) -> None:
    monkeypatch.setattr(sys, "argv", ["verify_provider", "--workspace-id", str(workspace_id)])
    monkeypatch.setattr(verify_provider, "get_settings", worker_settings)
    monkeypatch.setattr(verify_provider, "SessionFactory", SessionFactory)
    monkeypatch.setattr(
        "app.services.provider_adapter_resolver.build_provider_registry",
        lambda: registry_for(adapter),
    )
    verify_provider.main()


def adapter_for(graph: SyncGraph, **kwargs) -> SyntheticSeasonAdapter:
    return SyntheticSeasonAdapter(
        provider_league_id=graph.provider_league_id, dataset="alpha", **kwargs
    )


def stored_ciphertext(graph: SyncGraph) -> tuple[str | None, int | None]:
    with SessionFactory() as session:
        connection = session.get(ProviderConnection, graph.connection_id)
        assert connection is not None

        return connection.encrypted_credentials, connection.credential_version


def assert_no_secrets(output: str, graph: SyncGraph, ciphertext: str | None) -> None:
    for marker in (graph.swid, graph.espn_s2, KEY, ciphertext or "<none>"):
        assert marker not in output


def test_success_prints_sections_and_writes_nothing(
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = make_graph()
    before = stored_ciphertext(graph)
    adapter = adapter_for(graph)

    run_cli(monkeypatch, graph.workspace_id, adapter)

    output = capsys.readouterr().out
    for section in (
        "=== ACTIVE CONTEXT ===",
        "=== PROVIDER LEAGUE ===",
        "=== CAPABILITIES ===",
        "=== RESULT ===",
    ):
        assert section in output
    assert "Synthetic alpha League" in output
    assert "Credential source: stored" in output
    assert "No database rows were written." in output
    assert adapter.calls == ["validate_configuration", "fetch_league"]
    assert stored_ciphertext(graph) == before
    assert_no_secrets(output, graph, before[0])


def test_invalid_configuration_exits_one(
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = make_graph()
    before = stored_ciphertext(graph)
    adapter = RejectingAdapter(provider_league_id=graph.provider_league_id, dataset="alpha")

    with pytest.raises(SystemExit) as raised:
        run_cli(monkeypatch, graph.workspace_id, adapter)

    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "=== VALIDATION FAILED ===" in output
    assert "The fantasy provider credentials were rejected." in output
    assert "=== RESULT ===" not in output
    assert adapter.calls == ["validate_configuration"]
    assert stored_ciphertext(graph) == before
    assert_no_secrets(output, graph, before[0])


def test_resolution_failure_without_active_season_exits_one(
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = make_graph()
    ciphertext = stored_ciphertext(graph)[0]
    adapter = adapter_for(graph)

    with SessionFactory() as session:
        session.execute(
            update(Installation)
            .where(Installation.id == graph.workspace_id)
            .values(active_season_id=None)
        )
        session.commit()

    with pytest.raises(SystemExit) as raised:
        run_cli(monkeypatch, graph.workspace_id, adapter)

    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "=== VERIFICATION FAILED ===" in output
    assert "Credential values were not displayed." in output
    assert adapter.calls == []
    assert_no_secrets(output, graph, ciphertext)


def test_nonexistent_workspace_exits_one(
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = make_graph()
    adapter = adapter_for(graph)

    with pytest.raises(SystemExit) as raised:
        run_cli(monkeypatch, graph.workspace_id + 1_000_000_000, adapter)

    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "=== VERIFICATION FAILED ===" in output
    assert "Credential values were not displayed." in output
    assert adapter.calls == []


@pytest.mark.parametrize("failing_call", ["validate_configuration", "fetch_league"])
def test_provider_error_exits_one_without_credentials(
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failing_call: str,
) -> None:
    graph = make_graph()
    ciphertext = stored_ciphertext(graph)[0]
    adapter = adapter_for(graph, fail_on=failing_call)

    with pytest.raises(SystemExit) as raised:
        run_cli(monkeypatch, graph.workspace_id, adapter)

    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "=== VERIFICATION FAILED ===" in output
    assert "Credential values were not displayed." in output
    assert "=== RESULT ===" not in output
    assert_no_secrets(output, graph, ciphertext)
