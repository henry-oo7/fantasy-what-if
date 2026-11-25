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
    SyncStatus,
    Team,
)
from app.db.platform_enums import FantasyProvider
from app.db.session import SessionFactory
from app.providers.domain import ProviderMatchup
from app.services.provider_sync import synchronize_provider
from app.services.provider_sync_policy import ProviderSyncValidationError
from tests.support.provider_sync_fixtures import (
    SyncGraph,
    SyntheticSeasonAdapter,
    canonical_state,
    forbid_live_network,
    graph_factory,
    latest_run,
    sync_runs,
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


def test_incremental_provider_sync_orchestrates_canonical_writes(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    adapter = SyntheticSeasonAdapter(provider_league_id=graph.provider_league_id, dataset="alpha")

    result = synchronize_provider(
        session_factory=SessionFactory,
        season_id=graph.season_id,
        provider=adapter,
        full_sync=False,
        explicit_from_week=2,
        explicit_through_week=2,
    )

    assert result.season_id == graph.season_id
    assert result.provider is FantasyProvider.ESPN
    assert result.provider_league_id == graph.provider_league_id
    assert result.provider_season_id == "2026"
    assert result.current_week == 3
    assert result.full_sync is False
    assert result.superseded is False
    assert result.matchup_weeks == (2,)
    assert result.player_weeks == (2,)
    assert result.teams_upserted == 4
    assert result.matchups_upserted == 2
    assert result.fantasy_byes_upserted == 0
    assert result.player_week_scores_upserted == 4
    assert result.analytics_rebuilt is True

    assert adapter.calls == ["fetch_league", "fetch_matchups", "fetch_weekly_rosters"]

    state = canonical_state(graph)
    alpha_teams = [team for team in state["teams"] if team.startswith("alpha-")]
    assert alpha_teams == [f"alpha-{index}" for index in range(1, 5)]
    assert week_count(Matchup, graph.season_id, 2) == 2
    assert week_count(PlayerWeekScore, graph.season_id, 2) == 4
    assert week_count(OptimalLineupResult, graph.season_id, 2) == 4

    with SessionFactory() as session:
        team_count = session.scalar(
            select(func.count()).select_from(Team).where(Team.season_id == graph.season_id)
        )
    assert team_count is not None and team_count >= 4

    run = latest_run(graph.season_id)
    assert run.status is SyncStatus.SUCCEEDED
    assert run.provider is FantasyProvider.ESPN
    assert run.provider_connection_id == graph.connection_id
    assert run.requested_from_week == 2
    assert run.requested_through_week == 2
    assert run.teams_upserted == 4
    assert run.matchups_upserted == 2
    assert run.player_week_scores_upserted == 4
    assert run.analytics_rebuilt is True


def test_provider_must_match_season_connection(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    runs_before = len(sync_runs(graph.season_id))

    with pytest.raises(ValueError, match="does not match"):
        synchronize_provider(
            session_factory=SessionFactory,
            season_id=graph.season_id,
            provider=WrongProvider(provider_league_id=graph.provider_league_id, dataset="alpha"),
            full_sync=False,
            explicit_from_week=2,
            explicit_through_week=2,
        )

    assert len(sync_runs(graph.season_id)) == runs_before


def test_invalid_provider_data_rolls_back(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = canonical_state(graph)

    with pytest.raises(ProviderSyncValidationError):
        synchronize_provider(
            session_factory=SessionFactory,
            season_id=graph.season_id,
            provider=UnknownTeamMatchupAdapter(
                provider_league_id=graph.provider_league_id, dataset="alpha"
            ),
            full_sync=False,
            explicit_from_week=2,
            explicit_through_week=2,
        )

    assert issubclass(ProviderSyncValidationError, ValueError)
    assert canonical_state(graph) == before

    run = latest_run(graph.season_id)
    assert run.status is SyncStatus.FAILED
    assert run.safe_error_message == "The provider returned invalid synchronization data."
