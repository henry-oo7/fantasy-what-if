from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.platform_enums import (
    InstallationStatus,
)
from app.db.platform_models import Installation
from app.services.espn_sync import SyncResult
from app.services.provider_sync import ProviderSyncResult
from app.services.provider_sync_compat import (
    ActiveSynchronizationContextError,
    resolve_active_season_id,
    synchronize_active_provider,
)


class FakeScalarResult:
    def __init__(
        self,
        values: list[Installation],
    ) -> None:
        self.values = values

    def all(self) -> list[Installation]:
        return self.values


class FakeSession:
    def __init__(
        self,
        installations: list[Installation],
    ) -> None:
        self.installations = installations

    def scalars(
        self,
        statement,
    ) -> FakeScalarResult:
        del statement
        return FakeScalarResult(self.installations)


def make_installation(
    *,
    installation_id: int = 1,
    active_season_id: int | None = 1,
    status: InstallationStatus = (InstallationStatus.ACTIVE),
) -> Installation:
    return Installation(
        id=installation_id,
        application_name="Fantasy What If",
        status=status,
        active_league_id=1,
        active_season_id=active_season_id,
    )


def test_resolve_active_season_id() -> None:
    session = FakeSession([make_installation()])

    assert resolve_active_season_id(session) == 1


def test_missing_active_installation_is_rejected() -> None:
    session = FakeSession([])

    with pytest.raises(
        ActiveSynchronizationContextError,
        match="No active installation",
    ):
        resolve_active_season_id(session)


def test_multiple_active_installations_are_rejected() -> None:
    session = FakeSession(
        [
            make_installation(
                installation_id=1,
            ),
            make_installation(
                installation_id=2,
            ),
        ]
    )

    with pytest.raises(
        ActiveSynchronizationContextError,
        match="Multiple active installations",
    ):
        resolve_active_season_id(session)


def test_missing_active_season_is_rejected() -> None:
    session = FakeSession(
        [
            make_installation(
                active_season_id=None,
            )
        ]
    )

    with pytest.raises(
        ActiveSynchronizationContextError,
        match="active season",
    ):
        resolve_active_season_id(session)


def test_wrapper_preserves_legacy_sync_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_result = ProviderSyncResult(
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
        player_week_scores_upserted=394,
        analytics_rebuilt=False,
    )

    canonical_sync = Mock(return_value=canonical_result)

    monkeypatch.setattr(
        ("app.services.provider_sync_compat.resolve_active_season_id"),
        lambda session: 1,
    )
    monkeypatch.setattr(
        ("app.services.provider_sync_compat.synchronize_provider"),
        canonical_sync,
    )

    settings = SimpleNamespace(
        espn_league_id=529250970,
        espn_season=2026,
        espn_s2=SimpleNamespace(get_secret_value=lambda: "cookie"),
        espn_swid=SimpleNamespace(get_secret_value=lambda: "{swid}"),
    )

    result = synchronize_active_provider(
        session=Mock(spec=Session),
        settings=settings,
        full_sync=False,
    )

    assert isinstance(result, SyncResult)
    assert result.league_id == 529250970
    assert result.season == 2026
    assert result.current_week == 9
    assert result.full_sync is False
    assert result.player_weeks == (7, 8)
    assert result.teams_upserted == 12
    assert result.matchups_upserted == 72
    assert result.fantasy_byes_upserted == 0
    assert result.player_week_scores_upserted == 394

    canonical_sync.assert_called_once()
    call = canonical_sync.call_args.kwargs

    assert call["season_id"] == 1
    assert call["full_sync"] is False
