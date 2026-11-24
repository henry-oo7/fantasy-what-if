from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import (
    hash_token,
    tokens_match,
    verify_password,
)
from app.db.auth_models import (
    AuthSession,
    EmailVerificationToken,
    PasswordResetToken,
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
from app.repositories.account_token_repository import (
    AccountTokenRepository,
)
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository
from app.services.email_verification import (
    EmailVerificationError,
    verify_administrator_email,
)
from app.services.password_recovery import (
    PasswordRecoveryError,
    reset_administrator_password,
)
from tests.support.identity_email import (
    issue_email_verification_token,
    issue_password_reset_token,
)


@pytest.fixture
def database_session() -> Session:
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


def create_provisional_administrator(
    session: Session,
    *,
    verified: bool = False,
) -> tuple[Installation, User]:
    suffix = uuid4().hex[:12]
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)

    workspace = Installation(
        application_name="Token Lifecycle Test",
        slug=f"token-{suffix}",
        normalized_slug=f"token-{suffix}",
        timezone="America/Chicago",
        status=InstallationStatus.SETUP_IN_PROGRESS,
        registration_status=(WorkspaceRegistrationStatus.PROVISIONAL),
        onboarding_stage=(OnboardingStage.EMAIL_VERIFICATION_PENDING),
    )
    session.add(workspace)
    session.flush()

    user = AuthenticationRepository(session).create_user(
        username=f"token-admin-{suffix}",
        display_name="Token Administrator",
        email=f"token-admin-{suffix}@example.com",
        password="correct horse battery staple",
        role=UserRole.ADMIN,
        workspace_id=workspace.id,
    )

    if verified:
        user.email_verified_at = now

    session.flush()

    return workspace, user


def test_verification_issue_stores_only_hash(
    database_session: Session,
) -> None:
    _, user = create_provisional_administrator(database_session)
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)

    issued = issue_email_verification_token(
        database_session,
        user=user,
        lifetime_hours=24,
        now=now,
    )
    stored = database_session.scalar(
        select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id)
    )

    assert stored is not None
    assert stored.token_hash == hash_token(issued.raw_token)
    assert stored.token_hash != issued.raw_token
    assert tokens_match(
        issued.raw_token,
        stored.token_hash,
    )
    assert stored.expires_at == now + timedelta(hours=24)


def test_new_verification_token_invalidates_prior_token(
    database_session: Session,
) -> None:
    _, user = create_provisional_administrator(database_session)
    first_time = datetime(
        2026,
        11,
        20,
        18,
        0,
        tzinfo=UTC,
    )
    second_time = first_time + timedelta(minutes=5)

    first = issue_email_verification_token(
        database_session,
        user=user,
        lifetime_hours=24,
        now=first_time,
    )
    second = issue_email_verification_token(
        database_session,
        user=user,
        lifetime_hours=24,
        now=second_time,
    )

    with pytest.raises(
        EmailVerificationError,
        match="invalid or expired",
    ):
        verify_administrator_email(
            database_session,
            raw_token=first.raw_token,
            now=second_time,
        )

    verified = verify_administrator_email(
        database_session,
        raw_token=second.raw_token,
        now=second_time,
    )

    assert verified.email_verified_at == second_time


def test_verification_advances_provisional_onboarding(
    database_session: Session,
) -> None:
    workspace, user = create_provisional_administrator(database_session)
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    issued = issue_email_verification_token(
        database_session,
        user=user,
        lifetime_hours=24,
        now=now,
    )

    result = verify_administrator_email(
        database_session,
        raw_token=issued.raw_token,
        now=now + timedelta(minutes=1),
    )

    assert result.email_verified_at == (now + timedelta(minutes=1))
    assert workspace.registration_status is (WorkspaceRegistrationStatus.PROVISIONAL)
    assert workspace.onboarding_stage is (OnboardingStage.PROVIDER_SETUP_PENDING)


def test_verification_token_is_single_use(
    database_session: Session,
) -> None:
    _, user = create_provisional_administrator(database_session)
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    issued = issue_email_verification_token(
        database_session,
        user=user,
        lifetime_hours=24,
        now=now,
    )

    verify_administrator_email(
        database_session,
        raw_token=issued.raw_token,
        now=now,
    )

    with pytest.raises(
        EmailVerificationError,
        match="invalid or expired",
    ):
        verify_administrator_email(
            database_session,
            raw_token=issued.raw_token,
            now=now,
        )


def test_expired_verification_token_is_rejected(
    database_session: Session,
) -> None:
    _, user = create_provisional_administrator(database_session)
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    issued = issue_email_verification_token(
        database_session,
        user=user,
        lifetime_hours=1,
        now=now,
    )

    with pytest.raises(
        EmailVerificationError,
        match="invalid or expired",
    ):
        verify_administrator_email(
            database_session,
            raw_token=issued.raw_token,
            now=now + timedelta(hours=1),
        )


def test_password_reset_replaces_password_and_revokes_sessions(
    database_session: Session,
) -> None:
    _, user = create_provisional_administrator(
        database_session,
        verified=True,
    )
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    session_repository = SessionRepository(database_session)
    active_session = session_repository.create_session(
        user=user,
        lifetime_hours=24,
        now=now,
    )
    issued = issue_password_reset_token(
        database_session,
        user=user,
        lifetime_minutes=30,
        now=now,
    )

    result = reset_administrator_password(
        database_session,
        raw_token=issued.raw_token,
        new_password="replacement horse battery staple",
        now=now + timedelta(minutes=1),
    )

    assert result.user is user
    assert result.revoked_sessions == 1
    assert verify_password(
        "replacement horse battery staple",
        user.password_hash,
    )
    assert (
        session_repository.resolve_session(
            active_session.session_token,
            now=now + timedelta(minutes=1),
        )
        is None
    )


def test_password_reset_token_is_single_use(
    database_session: Session,
) -> None:
    _, user = create_provisional_administrator(
        database_session,
        verified=True,
    )
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    issued = issue_password_reset_token(
        database_session,
        user=user,
        lifetime_minutes=30,
        now=now,
    )

    reset_administrator_password(
        database_session,
        raw_token=issued.raw_token,
        new_password="replacement horse battery staple",
        now=now,
    )

    with pytest.raises(
        PasswordRecoveryError,
        match="invalid or expired",
    ):
        reset_administrator_password(
            database_session,
            raw_token=issued.raw_token,
            new_password="another replacement password",
            now=now,
        )


def test_expired_and_used_tokens_can_be_cleaned(
    database_session: Session,
) -> None:
    _, user = create_provisional_administrator(
        database_session,
        verified=True,
    )
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    repository = AccountTokenRepository(database_session)

    repository.create_email_verification_token(
        user_id=user.id,
        raw_token="expired-verification",
        expires_at=now - timedelta(seconds=1),
        now=now - timedelta(hours=1),
    )
    repository.create_password_reset_token(
        user_id=user.id,
        raw_token="used-reset",
        expires_at=now + timedelta(minutes=30),
        now=now,
    )
    reset_token = database_session.scalar(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    )

    assert reset_token is not None

    reset_token.used_at = now
    database_session.flush()

    deleted_count = repository.delete_expired_or_used_tokens(
        now=now,
    )

    assert deleted_count == 2
    assert (
        database_session.scalar(
            select(func.count(EmailVerificationToken.id)).where(
                EmailVerificationToken.user_id == user.id
            )
        )
        == 0
    )
    assert (
        database_session.scalar(
            select(func.count(PasswordResetToken.id)).where(PasswordResetToken.user_id == user.id)
        )
        == 0
    )


def test_raw_tokens_do_not_enter_session_records(
    database_session: Session,
) -> None:
    _, user = create_provisional_administrator(
        database_session,
        verified=True,
    )
    issued = issue_password_reset_token(
        database_session,
        user=user,
        lifetime_minutes=30,
    )

    serialized = " ".join(
        str(value)
        for value in database_session.scalars(select(AuthSession.session_token_hash)).all()
    )

    assert issued.raw_token not in serialized
