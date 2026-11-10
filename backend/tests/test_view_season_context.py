from unittest.mock import MagicMock

import pytest

from app.db.models import League, Season
from app.db.platform_enums import (
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import Installation
from app.services.active_season_context import (
    ViewSeasonContext,
    ViewSeasonResolutionError,
    resolve_view_season_context,
)


def installation(
    *,
    active_season_id: int | None = None,
) -> Installation:
    return Installation(
        id=1,
        application_name="Fantasy What-If",
        status=InstallationStatus.ACTIVE,
        active_league_id=10,
        active_season_id=active_season_id,
    )


def historical_season() -> Season:
    return Season(
        id=20,
        league_id=10,
        provider_season_id="2025",
        year=2025,
        current_week=18,
        regular_season_weeks=14,
        status=SeasonStatus.COMPLETED,
    )


def configured_session(
    *,
    current_installation: Installation,
    league: League | None = None,
    season: Season | None = None,
    historical_result: tuple[
        Season,
        League,
    ]
    | None = None,
) -> MagicMock:
    session = MagicMock()

    session.scalars.return_value.all.return_value = [current_installation]

    def get_model(
        model_type,
        model_id: int,
    ):
        if model_type is Season:
            if season is not None and season.id == model_id:
                return season

            return None

        if model_type is League:
            if league is not None and league.id == model_id:
                return league

            return None

        raise AssertionError(f"Unexpected lookup: {model_type} {model_id}")

    session.get.side_effect = get_model
    session.execute.return_value.first.return_value = historical_result

    return session


def test_historical_only_installation_uses_newest_imported_season() -> None:
    league = League(
        id=10,
        installation_id=1,
        name="Jimmy and Friends",
    )
    season = historical_season()

    context = resolve_view_season_context(
        session=configured_session(
            current_installation=installation(),
            historical_result=(
                season,
                league,
            ),
        )
    )

    assert context == ViewSeasonContext(
        installation_id=1,
        league_id=10,
        season_id=20,
        is_synchronization_active=False,
    )


def test_active_season_is_default_view() -> None:
    league = League(
        id=10,
        installation_id=1,
        name="Jimmy and Friends",
    )
    season = Season(
        id=21,
        league_id=10,
        provider_season_id="2026",
        year=2026,
        current_week=9,
        regular_season_weeks=14,
        status=SeasonStatus.ACTIVE,
    )

    context = resolve_view_season_context(
        session=configured_session(
            current_installation=installation(
                active_season_id=21,
            ),
            league=league,
            season=season,
        )
    )

    assert context == ViewSeasonContext(
        installation_id=1,
        league_id=10,
        season_id=21,
        is_synchronization_active=True,
    )


def test_explicit_historical_season_can_be_viewed() -> None:
    league = League(
        id=10,
        installation_id=1,
        name="Jimmy and Friends",
    )
    season = historical_season()

    context = resolve_view_season_context(
        session=configured_session(
            current_installation=installation(
                active_season_id=21,
            ),
            league=league,
            season=season,
        ),
        requested_season_id=20,
    )

    assert context.season_id == 20
    assert context.is_synchronization_active is False


def test_explicit_missing_season_is_rejected() -> None:
    with pytest.raises(
        ViewSeasonResolutionError,
        match="requested season does not exist",
    ):
        resolve_view_season_context(
            session=configured_session(
                current_installation=installation(),
            ),
            requested_season_id=999999,
        )


def test_historical_only_installation_requires_imported_season() -> None:
    with pytest.raises(
        ViewSeasonResolutionError,
        match="No imported season",
    ):
        resolve_view_season_context(
            session=configured_session(
                current_installation=installation(),
                historical_result=None,
            )
        )
