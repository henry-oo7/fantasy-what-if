"""Routine provider-synchronization service tests on committed data.

Real persistence in ``fantasy_what_if_test`` through ``SessionFactory``;
the provider is synthetic and in-process. Transaction-boundary and race
guarantees live in ``test_provider_sync_transaction_boundaries.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import (
    Matchup,
    OptimalLineupResult,
    PlayerWeekScore,
    Season,
    SyncStatus,
    Team,
)
from app.db.platform_enums import FantasyProvider, SeasonSyncState
from app.db.session import SessionFactory
from app.providers.domain import ProviderMatchup
from app.services.provider_sync import synchronize_season
from app.services.provider_sync_policy import ProviderSyncValidationError
from tests.support.provider_sync_fixtures import (
    SyncGraph,
    SyntheticSeasonAdapter,
    canonical_state,
    forbid_live_network,
    graph_factory,
    latest_run,
    registry_for,
    sync_runs,
    worker_settings,
)


@pytest.fixture
def make_graph() -> Iterator[Callable[[], SyncGraph]]:
    with graph_factory() as create:
        yield create


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


class WrongProvider(SyntheticSeasonAdapter):
    provider = FantasyProvider.SLEEPER


class UnknownTeamMatchupAdapter(SyntheticSeasonAdapter):
    def fetch_matchups(self, *, provider_league_id, provider_season_id, weeks):
        return (
            ProviderMatchup(
                week=weeks[0],
                home_provider_team_id=self._team_id(1),
                away_provider_team_id="unknown",
                home_score=Decimal("100"),
                away_score=Decimal("90"),
                is_complete=True,
                is_bye=False,
            ),
        )


def season_state(season_id: int) -> Season:
    with SessionFactory() as session:
        season = session.get(Season, season_id)
        assert season is not None
        session.expunge(season)

        return season


def week_count(model: type, season_id: int, week: int) -> int:
    with SessionFactory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.season_id == season_id, model.week == week)
            )
            or 0
        )


def test_season_sync_orchestrates_canonical_writes(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before_season = season_state(graph.season_id)
    adapter = SyntheticSeasonAdapter(provider_league_id=graph.provider_league_id, dataset="alpha")

    result = synchronize_season(
        session_factory=SessionFactory,
        season_id=graph.season_id,
        settings=worker_settings(),
        registry=registry_for(adapter),
    )

    assert result.season_id == graph.season_id
    assert result.provider is FantasyProvider.ESPN
    assert result.provider_league_id == graph.provider_league_id
    assert result.provider_season_id == "2026"
    assert result.current_week == 3
    assert result.full_sync is True
    assert result.superseded is False
    assert result.matchup_weeks == (1, 2, 3)
    assert result.player_weeks == (1, 2)
    assert result.teams_upserted == 4
    assert result.matchups_upserted == 6
    assert result.fantasy_byes_upserted == 0
    assert result.player_week_scores_upserted == 8
    assert result.analytics_rebuilt is True

    assert adapter.calls == ["fetch_league", "fetch_matchups", "fetch_weekly_rosters"]

    state = canonical_state(graph)
    alpha_teams = [team for team in state["teams"] if team.startswith("alpha-")]
    assert alpha_teams == [f"alpha-{index}" for index in range(1, 5)]
    for week in (1, 2, 3):
        assert week_count(Matchup, graph.season_id, week) == 2
    for week in (1, 2):
        assert week_count(PlayerWeekScore, graph.season_id, week) == 4
        assert week_count(OptimalLineupResult, graph.season_id, week) == 4

    with SessionFactory() as session:
        team_count = session.scalar(
            select(func.count()).select_from(Team).where(Team.season_id == graph.season_id)
        )
    assert team_count is not None and team_count >= 4

    run = latest_run(graph.season_id)
    assert run.status is SyncStatus.SUCCEEDED
    assert run.provider is FantasyProvider.ESPN
    assert run.provider_connection_id == graph.connection_id
    assert run.is_full_sync is True
    assert run.teams_upserted == 4
    assert run.matchups_upserted == 6
    assert run.player_week_scores_upserted == 8
    assert run.analytics_rebuilt is True

    season = season_state(graph.season_id)
    assert season.data_version == before_season.data_version + 1
    assert season.sync_state is SeasonSyncState.FRESH
    assert season.last_successful_sync_at is not None
    assert season.last_synced_at == season.last_successful_sync_at
    assert season.last_sync_failure_code is None


def test_adapter_provider_must_match_league_provider(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    runs_before = len(sync_runs(graph.season_id))
    before = canonical_state(graph)

    # The registry rejects a mismatched adapter (TypeError) before the
    # service's own ValueError check, which is therefore defense in depth.
    with pytest.raises(TypeError, match="different provider"):
        synchronize_season(
            session_factory=SessionFactory,
            season_id=graph.season_id,
            settings=worker_settings(),
            registry=registry_for(
                WrongProvider(provider_league_id=graph.provider_league_id, dataset="alpha")
            ),
        )

    assert len(sync_runs(graph.season_id)) == runs_before
    assert canonical_state(graph) == before


def test_invalid_provider_data_rolls_back(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = canonical_state(graph)
    before_season = season_state(graph.season_id)

    with pytest.raises(ProviderSyncValidationError):
        synchronize_season(
            session_factory=SessionFactory,
            season_id=graph.season_id,
            settings=worker_settings(),
            registry=registry_for(
                UnknownTeamMatchupAdapter(
                    provider_league_id=graph.provider_league_id, dataset="alpha"
                )
            ),
        )

    assert issubclass(ProviderSyncValidationError, ValueError)
    assert canonical_state(graph) == before

    run = latest_run(graph.season_id)
    assert run.status is SyncStatus.FAILED
    assert run.safe_error_message == "The provider returned invalid synchronization data."

    season = season_state(graph.season_id)
    assert season.data_version == before_season.data_version
    assert season.sync_state is SeasonSyncState.FAILED
    assert season.last_sync_failure_code == "invalid_provider_data"
