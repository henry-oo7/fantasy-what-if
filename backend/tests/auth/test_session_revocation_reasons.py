"""Repository and service coverage for persisted session revocation reasons."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.authentication import log_out_user
from app.db.auth_models import (
    AuthSession,
    SessionRevocationReason,
    User,
    UserRole,
)
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import engine
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import (
    SessionRepository,
    SessionResolutionFailure,
)
from app.services.password_recovery import (
    issue_password_reset_token,
    reset_administrator_password,
)

PASSWORD = "correct horse battery staple"


@pytest.fixture
def database_session() -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()

        if transaction.is_active:
            transaction.rollback()

        connection.close()


def create_user(
    session: Session,
    *,
    role: UserRole = UserRole.ADMIN,
) -> User:
    suffix = uuid4().hex[:12]
    workspace = Installation(
        application_name="Revocation Reason League",
        slug=f"revocation-{suffix}",
        normalized_slug=f"revocation-{suffix}",
        timezone="America/Chicago",
        status=InstallationStatus.ACTIVE,
        registration_status=WorkspaceRegistrationStatus.ACTIVE,
        onboarding_stage=OnboardingStage.COMPLETE,
    )
    session.add(workspace)
    session.flush()

    user = AuthenticationRepository(session).create_user(
        username=f"revocation-{suffix}",
        display_name="Revocation User",
        email=f"revocation-{suffix}@example.com" if role is UserRole.ADMIN else None,
        password=PASSWORD,
        role=role,
        workspace_id=workspace.id,
    )

    if role is UserRole.ADMIN:
        user.email_verified_at = datetime.now(UTC)
        session.flush()

    return user


def test_revocation_reason_is_required_for_bulk_revocation(
    database_session: Session,
) -> None:
    user = create_user(database_session)

    with pytest.raises(TypeError, match="reason"):
        SessionRepository(database_session).revoke_all_user_sessions(  # type: ignore[call-arg]
            user_id=user.id,
        )


def test_revocation_reason_is_required_for_single_revocation(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)
    created = repository.create_session(user=user, lifetime_hours=8)

    with pytest.raises(TypeError, match="reason"):
        repository.revoke_session(created.session)  # type: ignore[call-arg]

    with pytest.raises(TypeError, match="reason"):
        repository.revoke_session_by_token(created.session_token)  # type: ignore[call-arg]


def test_replacement_records_replaced_reason(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)
    first = repository.create_session(user=user, lifetime_hours=8)

    second = repository.replace_user_sessions(user=user, lifetime_hours=8)
    database_session.refresh(first.session)
    database_session.refresh(second.session)

    assert first.session.revocation_reason is SessionRevocationReason.REPLACED
    assert first.session.revoked_at is not None
    assert second.session.revocation_reason is None
    assert second.session.revoked_at is None


def test_replaced_session_resolves_as_replaced(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)
    first = repository.create_session(user=user, lifetime_hours=8)
    second = repository.replace_user_sessions(user=user, lifetime_hours=8)

    replaced = repository.resolve_session_state(first.session_token)
    active = repository.resolve_session_state(second.session_token)

    assert replaced.failure is SessionResolutionFailure.REPLACED
    assert replaced.resolved is None
    assert active.failure is None
    assert active.resolved is not None
    assert repository.resolve_session(first.session_token) is None


@pytest.mark.parametrize(
    "reason",
    [
        SessionRevocationReason.LOGOUT,
        SessionRevocationReason.PASSWORD_RESET,
        SessionRevocationReason.SPECTATOR_PASSWORD_RESET,
        SessionRevocationReason.SPECTATOR_DISABLED,
    ],
)
def test_non_replacement_revocations_resolve_as_invalid(
    database_session: Session,
    reason: SessionRevocationReason,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)
    created = repository.create_session(user=user, lifetime_hours=8)

    assert repository.revoke_session(created.session, reason=reason) is True
    database_session.refresh(created.session)

    resolution = repository.resolve_session_state(created.session_token)

    assert created.session.revocation_reason is reason
    assert resolution.failure is SessionResolutionFailure.INVALID
    assert resolution.resolved is None


@pytest.mark.parametrize(
    "raw_token",
    ["", "   ", "not-a-real-session-token"],
)
def test_missing_and_malformed_tokens_resolve_as_invalid(
    database_session: Session,
    raw_token: str,
) -> None:
    resolution = SessionRepository(database_session).resolve_session_state(raw_token)

    assert resolution.failure is SessionResolutionFailure.INVALID
    assert resolution.resolved is None


def test_expired_sessions_resolve_as_invalid_even_when_replaced(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)
    past = datetime.now(UTC) - timedelta(hours=10)
    expired_active = repository.create_session(user=user, lifetime_hours=1, now=past)
    expired_replaced = repository.create_session(user=user, lifetime_hours=1, now=past)
    repository.revoke_session(
        expired_replaced.session,
        reason=SessionRevocationReason.REPLACED,
        now=past,
    )

    for token in (expired_active.session_token, expired_replaced.session_token):
        resolution = repository.resolve_session_state(token)

        assert resolution.failure is SessionResolutionFailure.INVALID


def test_replaced_session_of_inactive_user_resolves_as_invalid(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)
    first = repository.create_session(user=user, lifetime_hours=8)
    repository.replace_user_sessions(user=user, lifetime_hours=8)

    user.is_active = False
    database_session.flush()

    resolution = repository.resolve_session_state(first.session_token)

    assert resolution.failure is SessionResolutionFailure.INVALID


def test_legacy_revoked_session_without_reason_resolves_as_invalid(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)
    created = repository.create_session(user=user, lifetime_hours=8)

    # A row revoked before the reason column existed keeps a NULL reason.
    database_session.execute(
        text(
            "UPDATE auth_sessions SET revoked_at = now(), revocation_reason = NULL WHERE id = :id"
        ),
        {"id": created.session.id},
    )
    database_session.expire_all()

    resolution = repository.resolve_session_state(created.session_token)

    assert resolution.failure is SessionResolutionFailure.INVALID


def test_earlier_revocation_reason_is_never_overwritten(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)
    first = repository.create_session(user=user, lifetime_hours=8)
    repository.replace_user_sessions(user=user, lifetime_hours=8)
    database_session.refresh(first.session)
    replaced_at = first.session.revoked_at
    assert replaced_at is not None

    # Simulate a caller holding stale in-memory state for the session.
    first.session.revoked_at = None

    repository.revoke_all_user_sessions(
        user_id=user.id,
        reason=SessionRevocationReason.PASSWORD_RESET,
    )
    assert repository.revoke_session(first.session, reason=SessionRevocationReason.LOGOUT) is False
    database_session.refresh(first.session)

    assert first.session.revocation_reason is SessionRevocationReason.REPLACED
    assert first.session.revoked_at == replaced_at


def test_reason_without_revocation_timestamp_violates_check_constraint(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    created = SessionRepository(database_session).create_session(user=user, lifetime_hours=8)

    with pytest.raises(IntegrityError) as exc_info:
        database_session.execute(
            text("UPDATE auth_sessions SET revocation_reason = 'logout' WHERE id = :id"),
            {"id": created.session.id},
        )

    assert exc_info.value.orig.diag.constraint_name == (
        "ck_auth_sessions_revocation_reason_requires_revoked_at"
    )


def test_administrator_session_replacement_records_replaced_reason(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    repository = SessionRepository(database_session)

    first = repository.replace_user_sessions(user=user, lifetime_hours=8)
    repository.replace_user_sessions(user=user, lifetime_hours=8)
    database_session.refresh(first.session)

    assert first.session.revocation_reason is SessionRevocationReason.REPLACED


def test_spectator_sessions_never_record_replacement(
    database_session: Session,
) -> None:
    spectator = create_user(database_session, role=UserRole.SPECTATOR)
    repository = SessionRepository(database_session)

    first = repository.create_session(user=spectator, lifetime_hours=8)
    second = repository.create_session(user=spectator, lifetime_hours=8)

    sessions = database_session.scalars(
        select(AuthSession).where(AuthSession.id.in_([first.session.id, second.session.id]))
    ).all()

    assert all(stored.revoked_at is None for stored in sessions)
    assert all(stored.revocation_reason is None for stored in sessions)


def test_log_out_user_records_logout_reason(
    database_session: Session,
) -> None:
    spectator = create_user(database_session, role=UserRole.SPECTATOR)
    login = SessionRepository(database_session).create_session(user=spectator, lifetime_hours=8)

    assert log_out_user(database_session, session_token=login.session_token) is True
    database_session.refresh(login.session)

    assert login.session.revocation_reason is SessionRevocationReason.LOGOUT


def test_password_reset_records_password_reset_reason(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    created = SessionRepository(database_session).create_session(user=user, lifetime_hours=8)
    issued = issue_password_reset_token(database_session, user=user, lifetime_minutes=30)

    completed = reset_administrator_password(
        database_session,
        raw_token=issued.raw_token,
        new_password="a completely new passphrase",
    )
    database_session.refresh(created.session)

    assert completed.revoked_sessions == 1
    assert created.session.revocation_reason is SessionRevocationReason.PASSWORD_RESET
    assert (
        SessionRepository(database_session).resolve_session_state(created.session_token).failure
        is SessionResolutionFailure.INVALID
    )
