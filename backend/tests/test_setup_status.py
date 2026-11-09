from unittest.mock import MagicMock

import pytest

from app.db.auth_models import User
from app.db.platform_enums import InstallationStatus
from app.db.platform_models import Installation
from app.services.setup_status import (
    SetupState,
    SetupStateError,
    resolve_setup_state,
)


def installation(
    *,
    installation_id: int = 1,
    status: InstallationStatus = (InstallationStatus.SETUP_REQUIRED),
    active_league_id: int | None = None,
    active_season_id: int | None = None,
) -> Installation:
    return Installation(
        id=installation_id,
        application_name="Fantasy What-If",
        status=status,
        timezone="America/Chicago",
        active_league_id=active_league_id,
        active_season_id=active_season_id,
    )


def setup_session(
    *,
    installations: list[Installation],
    user_count: int,
    provider_count: int = 0,
) -> MagicMock:
    session = MagicMock()

    session.scalars.return_value.all.return_value = installations

    session.scalar.side_effect = [
        user_count,
        provider_count,
    ]

    return session


def test_empty_database_allows_bootstrap() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[],
            user_count=0,
        )
    )

    assert state == SetupState(
        status=InstallationStatus.SETUP_REQUIRED,
        installation_exists=False,
        bootstrap_available=True,
        administrator_configured=False,
        provider_configured=False,
        active_league_configured=False,
    )


def test_administrator_advances_to_provider_setup() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[
                installation(
                    status=(InstallationStatus.SETUP_IN_PROGRESS),
                )
            ],
            user_count=1,
            provider_count=0,
        )
    )

    assert state.administrator_configured is True
    assert state.provider_configured is False
    assert state.active_league_configured is False
    assert state.bootstrap_available is False


def test_provider_advances_to_finalization() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[
                installation(
                    status=(InstallationStatus.SETUP_IN_PROGRESS),
                )
            ],
            user_count=1,
            provider_count=1,
        )
    )

    assert state.administrator_configured is True
    assert state.provider_configured is True
    assert state.active_league_configured is False


def test_active_installation_reports_complete_progress() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[
                installation(
                    status=InstallationStatus.ACTIVE,
                    active_league_id=1,
                    active_season_id=1,
                )
            ],
            user_count=2,
            provider_count=1,
        )
    )

    assert state == SetupState(
        status=InstallationStatus.ACTIVE,
        installation_exists=True,
        bootstrap_available=False,
        administrator_configured=True,
        provider_configured=True,
        active_league_configured=True,
    )


def test_partial_active_references_are_incomplete() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[
                installation(
                    status=(InstallationStatus.SETUP_IN_PROGRESS),
                    active_league_id=1,
                )
            ],
            user_count=1,
            provider_count=1,
        )
    )

    assert state.active_league_configured is False


def test_multiple_installations_are_rejected() -> None:
    with pytest.raises(
        SetupStateError,
        match="Multiple installations",
    ):
        resolve_setup_state(
            setup_session(
                installations=[
                    installation(
                        installation_id=1,
                    ),
                    installation(
                        installation_id=2,
                    ),
                ],
                user_count=0,
            )
        )


def test_user_model_is_part_of_count_contract() -> None:
    assert User.__tablename__ == "users"
