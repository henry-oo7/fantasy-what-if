"""Workspace-scoped shared spectator service (real PostgreSQL)."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.auth.security import verify_password
from app.db.auth_models import AuthSession, SessionRevocationReason, User, UserRole
from app.db.platform_enums import InstallationStatus
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.repositories.session_repository import SessionRepository
from app.services.spectator_account import (
    SPECTATOR_PASSWORD_MAX_LENGTH,
    SPECTATOR_PASSWORD_MIN_LENGTH,
    SpectatorAlreadyExistsError,
    SpectatorNotFoundError,
    SpectatorPasswordError,
    create_workspace_spectator,
    disable_workspace_spectator,
    enable_workspace_spectator,
    get_workspace_spectator,
    lock_workspace,
    reset_workspace_spectator_password,
    validate_spectator_password,
)

PASSWORD = "shared spectator passphrase 01"
NEW_PASSWORD = "replacement spectator phrase 02"


@pytest.fixture
def workspaces() -> Iterator[list[int]]:
    created: list[int] = []

    with SessionFactory() as session:
        for _ in range(2):
            slug = f"spectator-svc-{uuid4().hex[:10]}"
            workspace = Installation(
                application_name="Spectator Service",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=InstallationStatus.ACTIVE,
            )
            session.add(workspace)
            session.flush()
            created.append(workspace.id)

        session.commit()

    yield created

    with SessionFactory() as session:
        for workspace_id in created:
            session.execute(text("DELETE FROM installations WHERE id = :id"), {"id": workspace_id})

        session.commit()


def create(workspace_id: int, password: str = PASSWORD) -> User:
    with SessionFactory() as session:
        lock_workspace(session, workspace_id)
        spectator = create_workspace_spectator(
            session, workspace_id=workspace_id, password=password
        )
        session.commit()
        session.expunge(spectator)

        return spectator


def open_sessions(user_id: int, count: int) -> None:
    with SessionFactory() as session:
        user = session.get(User, user_id)
        assert user is not None

        for _ in range(count):
            SessionRepository(session).create_session(user=user, lifetime_hours=1)

        session.commit()


def sessions_of(user_id: int) -> list[AuthSession]:
    with SessionFactory() as session:
        rows = list(session.scalars(select(AuthSession).where(AuthSession.user_id == user_id)))
        session.expunge_all()

        return rows


@pytest.mark.parametrize(
    "password",
    [
        "x" * (SPECTATOR_PASSWORD_MIN_LENGTH - 1),
        "x" * (SPECTATOR_PASSWORD_MAX_LENGTH + 1),
    ],
)
def test_password_length_bounds(password: str) -> None:
    with pytest.raises(SpectatorPasswordError):
        validate_spectator_password(password)


def test_password_is_not_trimmed() -> None:
    # 13 visible characters padded by a space: valid only because nothing is trimmed.
    validate_spectator_password(" " + "x" * 13)


def test_create_generates_internal_username_and_hash_only(workspaces: list[int]) -> None:
    spectator = create(workspaces[0])

    assert spectator.role is UserRole.SPECTATOR
    assert spectator.workspace_id == workspaces[0]
    assert spectator.username.startswith("spectator-")
    assert len(spectator.username) == len("spectator-") + 32
    assert spectator.email is None
    assert spectator.is_active is True
    assert spectator.password_hash != PASSWORD
    assert PASSWORD not in spectator.password_hash
    assert verify_password(PASSWORD, spectator.password_hash)


def test_second_spectator_is_rejected(workspaces: list[int]) -> None:
    create(workspaces[0])

    with pytest.raises(SpectatorAlreadyExistsError):
        create(workspaces[0], NEW_PASSWORD)


def test_spectators_are_isolated_per_workspace(workspaces: list[int]) -> None:
    first = create(workspaces[0])
    second = create(workspaces[1], NEW_PASSWORD)

    assert first.id != second.id

    with SessionFactory() as session:
        found = get_workspace_spectator(session, workspace_id=workspaces[1])
        assert found is not None and found.id == second.id


def test_reset_replaces_hash_and_revokes_every_session(workspaces: list[int]) -> None:
    spectator = create(workspaces[0])
    other = create(workspaces[1], NEW_PASSWORD)
    open_sessions(spectator.id, 3)
    open_sessions(other.id, 1)

    with SessionFactory() as session:
        updated, revoked = reset_workspace_spectator_password(
            session, workspace_id=workspaces[0], password=NEW_PASSWORD
        )
        session.commit()
        assert revoked == 3
        assert verify_password(NEW_PASSWORD, updated.password_hash)
        assert not verify_password(PASSWORD, updated.password_hash)
        assert updated.is_active is True

    assert {row.revocation_reason for row in sessions_of(spectator.id)} == {
        SessionRevocationReason.SPECTATOR_PASSWORD_RESET
    }
    assert all(row.revoked_at is None for row in sessions_of(other.id))


def test_disable_revokes_with_spectator_disabled(workspaces: list[int]) -> None:
    spectator = create(workspaces[0])
    open_sessions(spectator.id, 2)

    with SessionFactory() as session:
        updated, revoked = disable_workspace_spectator(session, workspace_id=workspaces[0])
        session.commit()
        assert revoked == 2
        assert updated.is_active is False

    assert {row.revocation_reason for row in sessions_of(spectator.id)} == {
        SessionRevocationReason.SPECTATOR_DISABLED
    }


def test_enable_requires_and_applies_a_new_password(workspaces: list[int]) -> None:
    spectator = create(workspaces[0])

    with SessionFactory() as session:
        disable_workspace_spectator(session, workspace_id=workspaces[0])
        session.commit()

    with SessionFactory() as session:
        updated, _ = enable_workspace_spectator(
            session, workspace_id=workspaces[0], password=NEW_PASSWORD
        )
        session.commit()
        assert updated.id == spectator.id
        assert updated.is_active is True
        assert verify_password(NEW_PASSWORD, updated.password_hash)
        assert not verify_password(PASSWORD, updated.password_hash)


def test_missing_spectator_is_reported(workspaces: list[int]) -> None:
    with SessionFactory() as session, pytest.raises(SpectatorNotFoundError):
        reset_workspace_spectator_password(
            session, workspace_id=workspaces[0], password=NEW_PASSWORD
        )
