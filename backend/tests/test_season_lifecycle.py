from datetime import UTC, datetime

import pytest

from app.db.platform_enums import SeasonStatus
from app.services.season_lifecycle import (
    FutureSeasonImportError,
    ImportedSeasonKind,
    classify_imported_season,
)

NOW = datetime(
    2026,
    11,
    10,
    15,
    25,
    tzinfo=UTC,
)


def test_past_season_is_completed_and_not_synchronized() -> None:
    lifecycle = classify_imported_season(
        season_year=2025,
        now=NOW,
    )

    assert lifecycle.kind is ImportedSeasonKind.HISTORICAL
    assert lifecycle.status is SeasonStatus.COMPLETED
    assert lifecycle.synchronization_enabled is False
    assert lifecycle.activated_at is None
    assert lifecycle.completed_at == NOW


def test_current_season_is_active_and_synchronized() -> None:
    lifecycle = classify_imported_season(
        season_year=2026,
        now=NOW,
    )

    assert lifecycle.kind is ImportedSeasonKind.CURRENT
    assert lifecycle.status is SeasonStatus.ACTIVE
    assert lifecycle.synchronization_enabled is True
    assert lifecycle.activated_at == NOW
    assert lifecycle.completed_at is None


def test_future_season_is_rejected() -> None:
    with pytest.raises(
        FutureSeasonImportError,
        match=("Season 2027 cannot be imported before the 2027 calendar year"),
    ):
        classify_imported_season(
            season_year=2027,
            now=NOW,
        )


@pytest.mark.parametrize(
    "season_year",
    [
        0,
        1999,
        -1,
    ],
)
def test_invalid_provider_year_is_rejected(
    season_year: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="2000 or later",
    ):
        classify_imported_season(
            season_year=season_year,
            now=NOW,
        )
