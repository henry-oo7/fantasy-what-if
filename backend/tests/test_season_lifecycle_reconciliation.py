from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.db.models import Season
from app.db.platform_enums import (
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import Installation
from app.services.season_lifecycle_reconciliation import (
    SeasonLifecycleReconciliationError,
    reconcile_stale_active_season,
)

NEW_YEARS_DAY = datetime(
    2026,
    1,
    1,
    0,
    0,
    tzinfo=UTC,
)


def installation(
    *,
    installation_id: int = 1,
    active_season_id: int | None = 20,
) -> Installation:
    return Installation(
        id=installation_id,
        application_name="Fantasy What-If",
        status=InstallationStatus.ACTIVE,
        active_league_id=10,
        active_season_id=active_season_id,
    )


def season(
    *,
    year: int,
    status: SeasonStatus = SeasonStatus.ACTIVE,
) -> Season:
    return Season(
        id=20,
        league_id=10,
        year=year,
        current_week=17,
        regular_season_weeks=12,
        status=status,
        activated_at=datetime(
            year,
            8,
            1,
            tzinfo=UTC,
        ),
    )


def configured_session(
    *,
    installations: list[Installation],
    stored_season: Season | None = None,
) -> MagicMock:
    session = MagicMock()
    session.scalars.return_value.all.return_value = installations
    session.get.return_value = stored_season
    return session


def test_prior_year_active_season_is_completed() -> None:
    current_installation = installation()
    stale_season = season(year=2025)
    session = configured_session(
        installations=[current_installation],
        stored_season=stale_season,
    )

    result = reconcile_stale_active_season(
        session,
        now=NEW_YEARS_DAY,
    )

    assert result.changed is True
    assert result.completed_season_id == 20
    assert result.completed_season_year == 2025
    assert stale_season.status is SeasonStatus.COMPLETED
    assert stale_season.activated_at is None
    assert stale_season.completed_at == NEW_YEARS_DAY
    assert current_installation.active_season_id is None
    assert current_installation.active_league_id == 10
    session.flush.assert_called_once()
    session.commit.assert_called_once()


def test_existing_completion_timestamp_is_preserved() -> None:
    completed_at = datetime(
        2025,
        12,
        31,
        23,
        0,
        tzinfo=UTC,
    )
    stale_season = season(year=2025)
    stale_season.completed_at = completed_at
    session = configured_session(
        installations=[installation()],
        stored_season=stale_season,
    )

    reconcile_stale_active_season(
        session,
        now=NEW_YEARS_DAY,
    )

    assert stale_season.completed_at == completed_at


def test_current_year_active_season_is_unchanged() -> None:
    current_installation = installation()
    current_season = season(year=2026)
    session = configured_session(
        installations=[current_installation],
        stored_season=current_season,
    )

    result = reconcile_stale_active_season(
        session,
        now=NEW_YEARS_DAY,
    )

    assert result.changed is False
    assert current_season.status is SeasonStatus.ACTIVE
    assert current_season.activated_at is not None
    assert current_season.completed_at is None
    assert current_installation.active_season_id == 20
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_historical_only_installation_is_unchanged() -> None:
    session = configured_session(
        installations=[
            installation(
                active_season_id=None,
            )
        ],
    )

    result = reconcile_stale_active_season(
        session,
        now=NEW_YEARS_DAY,
    )

    assert result.changed is False
    session.get.assert_not_called()
    session.commit.assert_not_called()


def test_setup_without_active_installation_is_unchanged() -> None:
    session = configured_session(
        installations=[],
    )

    result = reconcile_stale_active_season(
        session,
        now=NEW_YEARS_DAY,
    )

    assert result.changed is False
    assert result.installation_id is None


def test_multiple_active_installations_are_rejected() -> None:
    session = configured_session(
        installations=[
            installation(
                installation_id=1,
            ),
            installation(
                installation_id=2,
            ),
        ],
    )

    with pytest.raises(
        SeasonLifecycleReconciliationError,
        match="Multiple active installations",
    ):
        reconcile_stale_active_season(
            session,
            now=NEW_YEARS_DAY,
        )


def test_missing_active_season_is_rejected() -> None:
    session = configured_session(
        installations=[installation()],
        stored_season=None,
    )

    with pytest.raises(
        SeasonLifecycleReconciliationError,
        match="missing season",
    ):
        reconcile_stale_active_season(
            session,
            now=NEW_YEARS_DAY,
        )
