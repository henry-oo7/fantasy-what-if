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
) -> Installation:
    return Installation(
        id=installation_id,
        application_name="Fantasy What-If",
        status=status,
        timezone="America/Chicago",
    )


def setup_session(
    *,
    installations: list[Installation],
    user_count: int,
) -> MagicMock:
    session = MagicMock()

    session.scalars.return_value.all.return_value = installations
    session.scalar.return_value = user_count

    return session


def test_empty_database_requires_setup_and_allows_bootstrap() -> None:
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
    )


def test_setup_required_installation_allows_bootstrap_without_users() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[installation()],
            user_count=0,
        )
    )

    assert state == SetupState(
        status=InstallationStatus.SETUP_REQUIRED,
        installation_exists=True,
        bootstrap_available=True,
    )


def test_existing_user_disables_bootstrap() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[installation()],
            user_count=1,
        )
    )

    assert state.bootstrap_available is False


def test_active_installation_permanently_disables_bootstrap() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[
                installation(
                    status=InstallationStatus.ACTIVE,
                )
            ],
            user_count=0,
        )
    )

    assert state == SetupState(
        status=InstallationStatus.ACTIVE,
        installation_exists=True,
        bootstrap_available=False,
    )


def test_setup_in_progress_is_reported() -> None:
    state = resolve_setup_state(
        setup_session(
            installations=[
                installation(
                    status=(InstallationStatus.SETUP_IN_PROGRESS),
                )
            ],
            user_count=0,
        )
    )

    assert state.status is (InstallationStatus.SETUP_IN_PROGRESS)
    assert state.bootstrap_available is True


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


def test_user_model_is_part_of_setup_count_contract() -> None:
    assert User.__tablename__ == "users"
