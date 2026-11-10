from unittest.mock import MagicMock

import pytest

from app.db.models import League, Season
from app.db.platform_enums import (
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import Installation
from app.services.active_season_context import (
    ActiveSeasonContext,
    ActiveSeasonResolutionError,
    resolve_active_season_context,
)


def installation(
    *,
    installation_id: int = 1,
    status: InstallationStatus = InstallationStatus.ACTIVE,
    active_league_id: int | None = 10,
    active_season_id: int | None = 20,
) -> Installation:
    return Installation(
        id=installation_id,
        application_name="Fantasy What-If",
        status=status,
        active_league_id=active_league_id,
        active_season_id=active_season_id,
    )


def configured_session(
    *,
    installations: list[Installation],
    league: League | None = None,
    season: Season | None = None,
) -> MagicMock:
    session = MagicMock()

    session.scalars.return_value.all.return_value = installations

    def get_model(
        model_type,
        model_id: int,
    ):
        if model_type is League:
            return league

        if model_type is Season:
            return season

        raise AssertionError(f"Unexpected model lookup: {model_type} {model_id}")

    session.get.side_effect = get_model

    return session


def test_resolves_active_internal_identities() -> None:
    league = League(
        id=10,
        name="Jimmy and Friends",
    )
    season = Season(
        id=20,
        league_id=10,
        year=2026,
        current_week=9,
        regular_season_weeks=12,
        status=SeasonStatus.ACTIVE,
    )

    context = resolve_active_season_context(
        session=configured_session(
            installations=[installation()],
            league=league,
            season=season,
        )
    )

    assert context == ActiveSeasonContext(
        installation_id=1,
        league_id=10,
        season_id=20,
    )


def test_rejects_missing_active_installation() -> None:
    session = configured_session(
        installations=[
            installation(
                status=InstallationStatus.SETUP_REQUIRED,
            )
        ],
    )

    with pytest.raises(
        ActiveSeasonResolutionError,
        match="No active installation",
    ):
        resolve_active_season_context(
            session=session,
        )


def test_rejects_multiple_active_installations() -> None:
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
        ActiveSeasonResolutionError,
        match="Multiple active installations",
    ):
        resolve_active_season_context(
            session=session,
        )


@pytest.mark.parametrize(
    (
        "active_league_id",
        "active_season_id",
        "message",
    ),
    [
        (
            None,
            20,
            "does not have an active league",
        ),
        (
            10,
            None,
            "does not have an active season",
        ),
    ],
)
def test_rejects_missing_active_identifiers(
    active_league_id: int | None,
    active_season_id: int | None,
    message: str,
) -> None:
    session = configured_session(
        installations=[
            installation(
                active_league_id=active_league_id,
                active_season_id=active_season_id,
            )
        ],
    )

    with pytest.raises(
        ActiveSeasonResolutionError,
        match=message,
    ):
        resolve_active_season_context(
            session=session,
        )


def test_rejects_missing_active_league() -> None:
    session = configured_session(
        installations=[installation()],
        league=None,
    )

    with pytest.raises(
        ActiveSeasonResolutionError,
        match="active league does not exist",
    ):
        resolve_active_season_context(
            session=session,
        )


def test_rejects_missing_active_season() -> None:
    league = League(
        id=10,
        name="Jimmy and Friends",
    )

    session = configured_session(
        installations=[installation()],
        league=league,
        season=None,
    )

    with pytest.raises(
        ActiveSeasonResolutionError,
        match="active season does not exist",
    ):
        resolve_active_season_context(
            session=session,
        )


def test_rejects_season_from_another_league() -> None:
    league = League(
        id=10,
        name="Jimmy and Friends",
    )
    season = Season(
        id=20,
        league_id=99,
        year=2026,
        current_week=9,
        regular_season_weeks=12,
    )

    session = configured_session(
        installations=[installation()],
        league=league,
        season=season,
    )

    with pytest.raises(
        ActiveSeasonResolutionError,
        match="does not belong",
    ):
        resolve_active_season_context(
            session=session,
        )
