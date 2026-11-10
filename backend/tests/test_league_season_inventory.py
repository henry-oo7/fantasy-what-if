from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.db.models import (
    League,
    Season,
    SeasonStatus,
)
from app.repositories.league_repository import (
    list_imported_seasons,
)


def test_imported_seasons_use_stable_reverse_year_order() -> None:
    session = MagicMock()

    league = League(
        id=7,
        installation_id=3,
        name="Jimmy and Friends",
    )

    current = Season(
        id=12,
        league_id=7,
        year=2026,
        current_week=9,
        regular_season_weeks=12,
        status=SeasonStatus.ACTIVE,
        last_synced_at=datetime(
            2026,
            11,
            9,
            tzinfo=UTC,
        ),
    )

    historical = Season(
        id=11,
        league_id=7,
        year=2025,
        current_week=18,
        regular_season_weeks=14,
        status=SeasonStatus.COMPLETED,
    )

    session.execute.return_value.all.return_value = [
        (current, league),
        (historical, league),
    ]

    seasons = list_imported_seasons(
        session,
        installation_id=3,
        active_season_id=12,
    )

    assert tuple(season.year for season in seasons) == (
        2026,
        2025,
    )

    assert seasons[0].is_active is True
    assert seasons[0].status == "active"
    assert seasons[1].is_active is False
    assert seasons[1].status == "completed"


def test_inventory_returns_no_unimported_seasons() -> None:
    session = MagicMock()
    session.execute.return_value.all.return_value = []

    seasons = list_imported_seasons(
        session,
        installation_id=3,
        active_season_id=12,
    )

    assert seasons == ()
