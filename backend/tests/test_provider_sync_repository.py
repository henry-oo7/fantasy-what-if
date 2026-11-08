from collections.abc import Iterable
from typing import Any

import pytest

from app.db.models import (
    League,
    Season,
    SyncRun,
    SyncStatus,
    Team,
)
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import (
    ProviderConnection,
)
from app.repositories.provider_sync_repository import (
    ProviderSyncContext,
    ProviderSyncContextError,
    ProviderSyncContextNotFoundError,
    ProviderSyncRepository,
)


class FakeScalarResult:
    def __init__(
        self,
        values: Iterable[Any],
    ) -> None:
        self.values = list(values)

    def all(self) -> list[Any]:
        return self.values


class FakeSession:
    def __init__(
        self,
        *,
        season: Season | None = None,
        league: League | None = None,
        provider_connection: (ProviderConnection | None) = None,
        teams: Iterable[Team] = (),
    ) -> None:
        self.season = season
        self.league = league
        self.provider_connection = provider_connection
        self.teams = list(teams)
        self.added: list[Any] = []
        self.flush_count = 0
        self.scalar_statement: Any = None

    def get(
        self,
        model: type[Any],
        object_id: int,
    ) -> Any:
        if model is Season:
            if self.season is not None and self.season.id == object_id:
                return self.season

            return None

        if model is League:
            if self.league is not None and self.league.id == object_id:
                return self.league

            return None

        if model is ProviderConnection:
            if self.provider_connection is not None and self.provider_connection.id == object_id:
                return self.provider_connection

            return None

        raise AssertionError(f"Unexpected model lookup: {model}")

    def scalars(
        self,
        statement: Any,
    ) -> FakeScalarResult:
        self.scalar_statement = statement

        return FakeScalarResult(self.teams)

    def add(
        self,
        value: Any,
    ) -> None:
        self.added.append(value)

        if isinstance(value, SyncRun):
            value.id = 99

    def flush(self) -> None:
        self.flush_count += 1


def make_context_objects() -> tuple[
    Season,
    League,
    ProviderConnection,
]:
    league = League(
        id=1,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        provider_league_id="529250970",
        name="Jimmy and Friends",
    )

    season = Season(
        id=2,
        league_id=1,
        year=2026,
        current_week=9,
        regular_season_weeks=12,
        provider_connection_id=3,
        provider_season_id="2026",
    )

    provider_connection = ProviderConnection(
        id=3,
        installation_id=1,
        provider=FantasyProvider.ESPN,
        display_name="ESPN",
        is_active=True,
    )

    return (
        season,
        league,
        provider_connection,
    )


def make_repository() -> tuple[
    ProviderSyncRepository,
    FakeSession,
]:
    season, league, provider_connection = make_context_objects()

    session = FakeSession(
        season=season,
        league=league,
        provider_connection=provider_connection,
    )

    return (
        ProviderSyncRepository(session),
        session,
    )


def test_load_context_uses_internal_season_id() -> None:
    repository, _ = make_repository()

    context = repository.load_context(
        season_id=2,
    )

    assert context.season_id == 2
    assert context.league_id == 1
    assert context.provider_connection_id == 3
    assert context.provider is FantasyProvider.ESPN
    assert context.provider_league_id == "529250970"
    assert context.provider_season_id == "2026"


def test_missing_season_is_reported() -> None:
    session = FakeSession()
    repository = ProviderSyncRepository(session)

    with pytest.raises(ProviderSyncContextNotFoundError):
        repository.load_context(
            season_id=999,
        )


def test_context_requires_provider_connection() -> None:
    season, league, _ = make_context_objects()
    season.provider_connection_id = None

    repository = ProviderSyncRepository(
        FakeSession(
            season=season,
            league=league,
        )
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="provider connection",
    ):
        repository.load_context(
            season_id=2,
        )


def test_context_rejects_inactive_connection() -> None:
    season, league, connection = make_context_objects()
    connection.is_active = False

    repository = ProviderSyncRepository(
        FakeSession(
            season=season,
            league=league,
            provider_connection=connection,
        )
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="inactive",
    ):
        repository.load_context(
            season_id=2,
        )


def test_context_rejects_provider_mismatch() -> None:
    season, league, connection = make_context_objects()
    connection.provider = FantasyProvider.SLEEPER

    repository = ProviderSyncRepository(
        FakeSession(
            season=season,
            league=league,
            provider_connection=connection,
        )
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="same provider",
    ):
        repository.load_context(
            season_id=2,
        )


def test_context_rejects_installation_mismatch() -> None:
    season, league, connection = make_context_objects()
    connection.installation_id = 9

    repository = ProviderSyncRepository(
        FakeSession(
            season=season,
            league=league,
            provider_connection=connection,
        )
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="different installations",
    ):
        repository.load_context(
            season_id=2,
        )


def test_provider_context_properties_require_ids() -> None:
    season, league, connection = make_context_objects()

    league.provider_league_id = None

    context = ProviderSyncContext(
        season=season,
        league=league,
        provider_connection=connection,
    )

    with pytest.raises(ProviderSyncContextError):
        _ = context.provider_league_id


def test_team_map_uses_string_provider_ids() -> None:
    repository, session = make_repository()

    session.teams = [
        Team(
            id=10,
            season_id=2,
            provider_team_id="1",
            name="Team 1",
            wins=0,
            losses=0,
            ties=0,
            points_for=0,
            points_against=0,
        ),
        Team(
            id=11,
            season_id=2,
            provider_team_id="2",
            name="Team 2",
            wins=0,
            losses=0,
            ties=0,
            points_for=0,
            points_against=0,
        ),
    ]

    team_map = repository.get_team_map(
        season_id=2,
    )

    assert set(team_map) == {
        "1",
        "2",
    }
    assert team_map["2"].id == 11


def test_team_map_rejects_missing_provider_id() -> None:
    repository, session = make_repository()

    session.teams = [
        Team(
            id=10,
            season_id=2,
            provider_team_id=None,
            name="Team 1",
            wins=0,
            losses=0,
            ties=0,
            points_for=0,
            points_against=0,
        )
    ]

    with pytest.raises(
        ProviderSyncContextError,
        match="canonical provider ID",
    ):
        repository.get_team_map(
            season_id=2,
        )


def test_create_provider_aware_sync_run() -> None:
    repository, session = make_repository()
    context = repository.load_context(
        season_id=2,
    )

    sync_run = repository.create_sync_run(
        context=context,
        is_full_sync=False,
        requested_weeks=(7, 8),
        provider_request_metadata={
            "matchup_weeks": [1, 2, 3],
        },
    )

    assert sync_run.id == 99
    assert sync_run.season_id == 2
    assert sync_run.provider_connection_id == 3
    assert sync_run.provider is FantasyProvider.ESPN
    assert sync_run.status is SyncStatus.RUNNING
    assert sync_run.is_full_sync is False
    assert sync_run.requested_from_week == 7
    assert sync_run.requested_through_week == 8
    assert sync_run.analytics_rebuilt is False
    assert sync_run.provider_request_metadata == {
        "matchup_weeks": [1, 2, 3],
    }

    assert session.added == [sync_run]
    assert session.flush_count == 1


def test_complete_sync_run_records_counts() -> None:
    repository, _ = make_repository()

    season, league, connection = make_context_objects()
    del league
    del connection

    sync_run = SyncRun(
        id=99,
        season_id=season.id,
        status=SyncStatus.RUNNING,
        is_full_sync=True,
    )
    sync_run.season = season

    repository.complete_sync_run(
        sync_run,
        teams_upserted=12,
        matchups_upserted=72,
        player_week_scores_upserted=1579,
        analytics_rebuilt=True,
    )

    assert sync_run.status is SyncStatus.SUCCEEDED
    assert sync_run.completed_at is not None
    assert sync_run.teams_upserted == 12
    assert sync_run.matchups_upserted == 72
    assert sync_run.player_week_scores_upserted == 1579
    assert sync_run.analytics_rebuilt is True
    assert season.last_synced_at is not None


def test_fail_sync_run_truncates_safe_message() -> None:
    repository, _ = make_repository()

    sync_run = SyncRun(
        id=99,
        season_id=2,
        status=SyncStatus.RUNNING,
        is_full_sync=False,
    )

    repository.fail_sync_run(
        sync_run,
        safe_error_message="x" * 1200,
    )

    assert sync_run.status is SyncStatus.FAILED
    assert sync_run.completed_at is not None
    assert len(sync_run.safe_error_message) == 1000
    assert sync_run.analytics_rebuilt is False
