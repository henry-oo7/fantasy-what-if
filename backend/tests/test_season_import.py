"""Season import service tests on committed data.

Real persistence in ``fantasy_what_if_test`` through ``SessionFactory``;
providers are synthetic and in-process. The critical import races and
transaction boundaries live in ``test_provider_sync_transaction_boundaries.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, func, select, update

from app.db.models import Season, SyncRun
from app.db.platform_enums import FantasyProvider, SeasonStatus, WorkspaceRegistrationStatus
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.db.session import engine as default_engine
from app.providers.credential_keyring import ProviderCredentialKeyring
from app.providers.domain import ProviderCredentials
from app.providers.registry import ProviderRegistry
from app.services.season_import import (
    SEASON_IMPORT_LOCK_KEY,
    SeasonImportConflictError,
    SeasonImportUnavailableError,
    SeasonImportValidationError,
    import_provider_season,
)
from tests.support.provider_sync_fixtures import (
    KEY,
    SyncGraph,
    SyntheticSeasonAdapter,
    canonical_state,
    forbid_live_network,
    graph_factory,
)

NOW = datetime(2026, 11, 10, tzinfo=UTC)
KEYRING = ProviderCredentialKeyring(keys={1: KEY}, current_version=1)


@pytest.fixture
def make_graph() -> Iterator[Callable[[], SyncGraph]]:
    with graph_factory() as create:
        yield create


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


class Registry:
    """ESPN registry that records created adapters and their credentials."""

    def __init__(self, adapter_factory: Callable[[], SyntheticSeasonAdapter]) -> None:
        self.adapter_factory = adapter_factory
        self.credentials: list[dict[str, str]] = []
        self.adapters: list[SyntheticSeasonAdapter] = []

    def build(self) -> ProviderRegistry:
        registry = ProviderRegistry()

        def create(credentials: ProviderCredentials) -> SyntheticSeasonAdapter:
            adapter = self.adapter_factory()
            self.credentials.append(dict(credentials.values))
            self.adapters.append(adapter)

            return adapter

        registry.register(FantasyProvider.ESPN, create)

        return registry


def registry_for_year(graph: SyncGraph, year: int) -> Registry:
    return Registry(
        lambda: SyntheticSeasonAdapter(
            provider_league_id=graph.provider_league_id, dataset="imp", season_year=year
        )
    )


def seasons_of(graph: SyncGraph) -> dict[int, Season]:
    with SessionFactory() as session:
        seasons = session.scalars(select(Season).where(Season.league_id == graph.league_id)).all()
        for season in seasons:
            session.expunge(season)

        return {season.year: season for season in seasons}


def load_workspace(workspace_id: int) -> Installation:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)
        assert workspace is not None
        session.expunge(workspace)

        return workspace


def season_count(graph: SyncGraph) -> int:
    return len(seasons_of(graph))


def test_historical_import_is_completed_without_active_sync(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    registry = registry_for_year(graph, 2025)

    result = import_provider_season(
        SessionFactory,
        workspace_id=graph.workspace_id,
        season_year=2025,
        credential_keyring=KEYRING,
        registry=registry.build(),
        now=NOW,
    )

    assert result.status is SeasonStatus.COMPLETED
    assert result.synchronization_enabled is False
    assert result.year == 2025
    assert result.sync_result.teams_upserted == 4

    workspace = load_workspace(graph.workspace_id)
    assert workspace.active_season_id == graph.season_id
    assert workspace.active_league_id == graph.league_id

    imported = seasons_of(graph)[2025]
    assert imported.id == result.season_id
    assert imported.status is SeasonStatus.COMPLETED
    assert imported.completed_at == NOW
    assert imported.activated_at is None
    assert seasons_of(graph)[2026].status is SeasonStatus.ACTIVE

    assert registry.credentials == [
        {
            "league_id": graph.provider_league_id,
            "season": "2025",
            "espn_s2": graph.espn_s2,
            "swid": graph.swid,
        }
    ]

    with SessionFactory() as session:
        runs = session.scalars(select(SyncRun).where(SyncRun.season_id == imported.id)).all()
    assert len(runs) == 1 and runs[0].status.value == "succeeded"


def test_current_import_becomes_active_and_completes_previous_active(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()

    with SessionFactory() as session:
        session.execute(update(Season).where(Season.id == graph.season_id).values(year=2025))
        session.commit()

    registry = registry_for_year(graph, 2026)

    result = import_provider_season(
        SessionFactory,
        workspace_id=graph.workspace_id,
        season_year=2026,
        credential_keyring=KEYRING,
        registry=registry.build(),
        now=NOW,
    )

    assert result.status is SeasonStatus.ACTIVE
    assert result.synchronization_enabled is True

    workspace = load_workspace(graph.workspace_id)
    assert workspace.active_season_id == result.season_id

    seasons = seasons_of(graph)
    assert seasons[2026].status is SeasonStatus.ACTIVE
    assert seasons[2026].activated_at == NOW
    assert seasons[2025].status is SeasonStatus.COMPLETED
    assert seasons[2025].completed_at == NOW


def test_duplicate_year_is_rejected_before_provider_creation(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    registry = registry_for_year(graph, 2026)
    before = canonical_state(graph)

    with pytest.raises(SeasonImportConflictError, match="already imported"):
        import_provider_season(
            SessionFactory,
            workspace_id=graph.workspace_id,
            season_year=2026,
            credential_keyring=KEYRING,
            registry=registry.build(),
            now=NOW,
        )

    assert registry.adapters == []
    assert season_count(graph) == 1
    assert canonical_state(graph) == before


def test_future_year_is_rejected_before_database_access() -> None:
    session_factory = MagicMock()

    with pytest.raises(
        SeasonImportValidationError,
        match="Season 2027 cannot be imported before the 2027 calendar year",
    ):
        import_provider_season(
            session_factory,
            workspace_id=1,
            season_year=2027,
            credential_keyring=KEYRING,
            now=NOW,
        )

    session_factory.assert_not_called()


def test_provider_returning_different_year_is_rejected_and_nothing_persists(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = canonical_state(graph)

    class WrongYearAdapter(SyntheticSeasonAdapter):
        def fetch_league(self, *, provider_league_id, provider_season_id):
            return SyntheticSeasonAdapter(
                provider_league_id=self.provider_league_id, dataset="imp", season_year=2024
            ).fetch_league(provider_league_id=provider_league_id, provider_season_id="2024")

    registry = Registry(
        lambda: WrongYearAdapter(
            provider_league_id=graph.provider_league_id, dataset="imp", season_year=2025
        )
    )

    with pytest.raises(SeasonImportValidationError, match="different season"):
        import_provider_season(
            SessionFactory,
            workspace_id=graph.workspace_id,
            season_year=2025,
            credential_keyring=KEYRING,
            registry=registry.build(),
            now=NOW,
        )

    assert set(seasons_of(graph)) == {2026}
    assert canonical_state(graph) == before


def test_import_lock_is_taken_only_in_persistence_transaction(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    events: list[str] = []
    lock_parameters: list[object] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        if "pg_advisory_xact_lock" in statement:
            events.append("import_lock")
            lock_parameters.append(parameters)

    registry = Registry(
        lambda: SyntheticSeasonAdapter(
            provider_league_id=graph.provider_league_id,
            dataset="imp",
            season_year=2025,
            before_call=lambda name: events.append(f"provider:{name}"),
        )
    )

    event.listen(default_engine, "before_cursor_execute", record)

    try:
        import_provider_season(
            SessionFactory,
            workspace_id=graph.workspace_id,
            season_year=2025,
            credential_keyring=KEYRING,
            registry=registry.build(),
            now=NOW,
        )
    finally:
        event.remove(default_engine, "before_cursor_execute", record)

    assert events.count("import_lock") == 1
    provider_events = [index for index, name in enumerate(events) if name.startswith("provider:")]
    assert provider_events
    assert events.index("import_lock") > max(provider_events)
    assert lock_parameters == [
        {"lock_key": SEASON_IMPORT_LOCK_KEY, "workspace_id": graph.workspace_id}
    ]


def test_import_never_reads_another_workspace(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    other = make_graph()
    other_before = canonical_state(other)
    other_seasons = {year: season.id for year, season in seasons_of(other).items()}

    with SessionFactory() as session:
        missing_id = int(session.scalar(select(func.max(Installation.id))) or 0) + 1000

    registry = registry_for_year(graph, 2025)

    with pytest.raises(SeasonImportUnavailableError, match="active workspace"):
        import_provider_season(
            SessionFactory,
            workspace_id=missing_id,
            season_year=2025,
            credential_keyring=KEYRING,
            registry=registry.build(),
            now=NOW,
        )

    assert registry.adapters == []

    import_provider_season(
        SessionFactory,
        workspace_id=graph.workspace_id,
        season_year=2025,
        credential_keyring=KEYRING,
        registry=registry.build(),
        now=NOW,
    )

    assert {year: season.id for year, season in seasons_of(other).items()} == other_seasons
    assert canonical_state(other) == other_before
    assert registry.credentials[0]["league_id"] == graph.provider_league_id


def test_provisional_workspace_cannot_import(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()

    with SessionFactory() as session:
        session.execute(
            update(Installation)
            .where(Installation.id == graph.workspace_id)
            .values(registration_status=WorkspaceRegistrationStatus.PROVISIONAL)
        )
        session.commit()

    registry = registry_for_year(graph, 2025)

    with pytest.raises(SeasonImportUnavailableError, match="active workspace"):
        import_provider_season(
            SessionFactory,
            workspace_id=graph.workspace_id,
            season_year=2025,
            credential_keyring=KEYRING,
            registry=registry.build(),
            now=NOW,
        )

    assert registry.adapters == []
    assert set(seasons_of(graph)) == {2026}
