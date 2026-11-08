from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.db.platform_enums import FantasyProvider
from app.services.provider_adapter_resolver import (
    ActiveProviderContext,
    ProviderAdapterResolutionError,
)
from app.services.provider_sync import ProviderSyncResult
from app.services.provider_sync_compat import (
    ActiveSynchronizationContextError,
    synchronize_active_provider,
)


def test_context_error_remains_compatible() -> None:
    assert issubclass(
        ActiveSynchronizationContextError,
        ProviderAdapterResolutionError,
    )


def test_wrapper_preserves_legacy_sync_result(
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
        credentials_source="environment_fallback",
    )

    fake_provider = SimpleNamespace(
        provider=FantasyProvider.ESPN,
    )
    session = Mock(spec=Session)
    settings = SimpleNamespace()

    resolver = Mock(
        return_value=(
            context,
            fake_provider,
        )
    )
    canonical_sync = Mock(return_value=canonical_result)

    monkeypatch.setattr(
        ("app.services.provider_sync_compat.resolve_active_provider_adapter"),
        resolver,
    )
    monkeypatch.setattr(
        ("app.services.provider_sync_compat.synchronize_provider"),
        canonical_sync,
    )

    result = synchronize_active_provider(
        session=session,
        settings=settings,
        full_sync=False,
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

    resolver.assert_called_once_with(
        session=session,
        settings=settings,
    )

    canonical_sync.assert_called_once_with(
        session=session,
        season_id=1,
        provider=fake_provider,
        full_sync=False,
    )


def test_wrapper_propagates_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Mock(spec=Session)
    settings = SimpleNamespace()

    def reject_resolution(**kwargs):
        del kwargs

        raise ProviderAdapterResolutionError("The active fantasy provider is not implemented.")

    monkeypatch.setattr(
        ("app.services.provider_sync_compat.resolve_active_provider_adapter"),
        reject_resolution,
    )

    with pytest.raises(
        ProviderAdapterResolutionError,
        match="not implemented",
    ):
        synchronize_active_provider(
            session=session,
            settings=settings,
            full_sync=False,
        )
