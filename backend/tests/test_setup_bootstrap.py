from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.db.auth_models import User, UserRole
from app.db.platform_enums import InstallationStatus
from app.db.platform_models import Installation
from app.services.setup_bootstrap import (
    SETUP_BOOTSTRAP_LOCK_KEY,
    SetupBootstrapStateError,
    SetupBootstrapUnavailableError,
    bootstrap_administrator,
)


def installation(
    *,
    installation_id: int = 1,
    status: InstallationStatus = (InstallationStatus.SETUP_REQUIRED),
) -> Installation:
    return Installation(
        id=installation_id,
        application_name="Fantasy What-If",
        timezone="America/Chicago",
        status=status,
    )


def administrator() -> User:
    return User(
        id=10,
        username="first-admin",
        display_name="First Administrator",
        email="admin@example.com",
        password_hash="$argon2id$test-placeholder",
        role=UserRole.ADMIN,
        is_active=True,
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


def test_bootstrap_creates_admin_and_starts_setup() -> None:
    session = setup_session(
        installations=[installation()],
        user_count=0,
    )
    created_admin = administrator()
    timestamp = datetime(2026, 11, 9, tzinfo=UTC)

    with patch("app.services.setup_bootstrap.AuthenticationRepository") as repository_type:
        repository_type.return_value.create_user.return_value = created_admin

        stored_installation, stored_admin = bootstrap_administrator(
            session,
            username="first-admin",
            display_name="First Administrator",
            email="admin@example.com",
            password="correct horse battery staple",
            now=timestamp,
        )

    assert stored_admin is created_admin
    assert stored_installation.status is (InstallationStatus.SETUP_IN_PROGRESS)
    assert stored_installation.setup_started_at == timestamp

    repository_type.return_value.create_user.assert_called_once_with(
        username="first-admin",
        display_name="First Administrator",
        email="admin@example.com",
        password="correct horse battery staple",
        role=UserRole.ADMIN,
        is_active=True,
    )

    statement_text = str(session.execute.call_args.args[0])

    assert "pg_advisory_xact_lock" in statement_text
    assert session.execute.call_args.args[1] == {
        "lock_key": SETUP_BOOTSTRAP_LOCK_KEY,
    }


def test_bootstrap_creates_installation_when_missing() -> None:
    session = setup_session(
        installations=[],
        user_count=0,
    )

    with patch("app.services.setup_bootstrap.AuthenticationRepository") as repository_type:
        repository_type.return_value.create_user.return_value = administrator()

        stored_installation, _ = bootstrap_administrator(
            session,
            username="first-admin",
            display_name="First Administrator",
            password="correct horse battery staple",
        )

    assert stored_installation.status is (InstallationStatus.SETUP_IN_PROGRESS)
    assert stored_installation.setup_started_at is not None
    session.add.assert_called_once_with(stored_installation)
    session.flush.assert_called_once()


def test_existing_user_blocks_bootstrap() -> None:
    session = setup_session(
        installations=[installation()],
        user_count=1,
    )

    with pytest.raises(
        SetupBootstrapUnavailableError,
        match="no longer available",
    ):
        bootstrap_administrator(
            session,
            username="first-admin",
            display_name="First Administrator",
            password="correct horse battery staple",
        )


def test_active_installation_blocks_bootstrap() -> None:
    session = setup_session(
        installations=[
            installation(
                status=InstallationStatus.ACTIVE,
            )
        ],
        user_count=0,
    )

    with pytest.raises(
        SetupBootstrapUnavailableError,
        match="no longer available",
    ):
        bootstrap_administrator(
            session,
            username="first-admin",
            display_name="First Administrator",
            password="correct horse battery staple",
        )


def test_multiple_installations_are_rejected() -> None:
    session = setup_session(
        installations=[
            installation(installation_id=1),
            installation(installation_id=2),
        ],
        user_count=0,
    )

    with pytest.raises(
        SetupBootstrapStateError,
        match="Multiple installations",
    ):
        bootstrap_administrator(
            session,
            username="first-admin",
            display_name="First Administrator",
            password="correct horse battery staple",
        )
