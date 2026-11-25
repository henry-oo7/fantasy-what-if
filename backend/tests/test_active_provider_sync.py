from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from app.db.platform_enums import FantasyProvider
from app.services.active_provider_sync import (
    synchronize_active_season,
)
from app.services.provider_adapter_resolver import (
    ActiveProviderContext,
    ProviderAdapterResolutionError,
)
from app.services.provider_sync import ProviderSyncResult


def make_session_factory(session: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__enter__.return_value = session

    return MagicMock(return_value=context)


def test_active_sync_returns_provider_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_result = ProviderSyncResult(
        provider=FantasyProvider.ESPN,
        season_id=1,
        provider_league_id="529250970",
        provider_season_id="2026",
        current_week=9,
        full_sync=False,
        matchup_weeks=tuple(range(1, 13)),
        player_weeks=(7, 8),
        teams_upserted=12,
        matchups_upserted=72,
        fantasy_byes_upserted=0,
        player_week_scores_upserted=401,
        analytics_rebuilt=False,
    )

    context = ActiveProviderContext(
        installation_id=1,
        season_id=1,
        provider_connection_id=1,
        provider=FantasyProvider.ESPN,
        credentials_source="stored",
    )

    fake_provider = SimpleNamespace(
        provider=FantasyProvider.ESPN,
    )
    session = MagicMock()
    session_factory = make_session_factory(session)
    settings = SimpleNamespace()

    resolver = Mock(
        return_value=(
            context,
            fake_provider,
        )
    )
    plan = object()
    prepare = Mock(return_value=plan)
    execute = Mock(return_value=canonical_result)
    reconcile = Mock()

    monkeypatch.setattr(
        ("app.services.active_provider_sync.reconcile_stale_active_season"),
        reconcile,
    )
    monkeypatch.setattr(
        ("app.services.active_provider_sync.resolve_active_provider_adapter"),
        resolver,
    )
    monkeypatch.setattr(
        ("app.services.active_provider_sync.prepare_provider_sync"),
        prepare,
    )
    monkeypatch.setattr(
        ("app.services.active_provider_sync.execute_provider_sync"),
        execute,
    )

    registry = object()

    result = synchronize_active_season(
        session_factory=session_factory,
        workspace_id=1,
        settings=settings,
        full_sync=False,
        registry=registry,  # type: ignore[arg-type]
    )

    assert isinstance(
        result,
        ProviderSyncResult,
    )
    assert result.provider is FantasyProvider.ESPN
    assert result.season_id == 1
    assert result.provider_league_id == "529250970"
    assert result.provider_season_id == "2026"
    assert result.current_week == 9
    assert result.full_sync is False
    assert result.player_weeks == (7, 8)
    assert result.teams_upserted == 12
    assert result.matchups_upserted == 72
    assert result.fantasy_byes_upserted == 0
    assert result.player_week_scores_upserted == 401

    reconcile.assert_called_once_with(
        session,
        workspace_id=1,
    )

    resolver.assert_called_once_with(
        session=session,
        workspace_id=1,
        settings=settings,
        registry=registry,
    )

    prepare.assert_called_once()
    assert prepare.call_args.args == (session,)
    assert prepare.call_args.kwargs["season_id"] == 1
    assert prepare.call_args.kwargs["provider"] is fake_provider
    assert prepare.call_args.kwargs["full_sync"] is False
    assert prepare.call_args.kwargs["job_authority"] is None

    session.commit.assert_called_once_with()
    execute.assert_called_once_with(
        session_factory=session_factory,
        plan=plan,
        provider=fake_provider,
    )


def test_active_sync_propagates_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session_factory = make_session_factory(session)
    settings = SimpleNamespace()

    reconcile = Mock()
    prepare = Mock()
    execute = Mock()
    monkeypatch.setattr("app.services.active_provider_sync.prepare_provider_sync", prepare)
    monkeypatch.setattr("app.services.active_provider_sync.execute_provider_sync", execute)

    monkeypatch.setattr(
        ("app.services.active_provider_sync.reconcile_stale_active_season"),
        reconcile,
    )

    def reject_resolution(**kwargs):
        del kwargs

        raise ProviderAdapterResolutionError("The active fantasy provider is not implemented.")

    monkeypatch.setattr(
        ("app.services.active_provider_sync.resolve_active_provider_adapter"),
        reject_resolution,
    )

    with pytest.raises(
        ProviderAdapterResolutionError,
        match="not implemented",
    ):
        synchronize_active_season(
            session_factory=session_factory,
            workspace_id=1,
            settings=settings,
            full_sync=False,
            registry=None,
        )

    reconcile.assert_called_once_with(
        session,
        workspace_id=1,
    )
    prepare.assert_not_called()
    execute.assert_not_called()
    session.commit.assert_not_called()
