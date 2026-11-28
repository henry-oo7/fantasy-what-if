"""PostgreSQL concurrency, rollback, and constraint tests for identity flows.

Each worker uses its own SessionFactory session, therefore its own
PostgreSQL connection and transaction.
"""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.authentication import log_in_by_email
from app.auth.security import InvalidPasswordError, hash_token, verify_password
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
    RegistrationMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import SessionFactory, engine
from app.repositories.account_token_repository import AccountTokenRepository
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository
from app.services.account_registration import (
    DuplicateAccountEmailError,
    register_personal_account,
)
from app.services.password_recovery import reset_password
from tests.support.identity_email import (
    delete_users,
    email_jobs_for,
    identity_deliveries,
    issue_password_reset_token,
)

ADMIN_PASSWORD = "correct horse battery staple"


def run_concurrently(
    worker: Callable[[int], object],
    count: int,
) -> list[object]:
    """Release all workers at once and return results or exceptions."""

    barrier = threading.Barrier(count)

    def synchronized(index: int) -> object:
        barrier.wait(timeout=10)

        try:
            return worker(index)
        except Exception as exc:  # noqa: BLE001 - results are asserted
            return exc

    with ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(synchronized, range(count)))


def delete_workspaces(workspace_ids: list[int]) -> None:
    with SessionFactory() as session:
        for workspace_id in workspace_ids:
            for user in session.scalars(select(User).where(User.workspace_id == workspace_id)):
                session.delete(user)

            session.flush()

            workspace = session.get(Installation, workspace_id)

            if workspace is not None:
                session.delete(workspace)

        session.commit()


def delete_registrations_by_slug(slugs: list[str]) -> None:
    with SessionFactory() as session:
        workspace_ids = list(
            session.scalars(
                select(Installation.id).where(Installation.normalized_slug.in_(slugs))
            ).all()
        )

    delete_workspaces(workspace_ids)


@pytest.fixture
def active_administrator() -> Iterator[tuple[int, str]]:
    suffix = uuid4().hex[:12]
    email = f"concurrency-admin-{suffix}@example.com"

    with SessionFactory() as session:
        workspace = Installation(
            application_name="Concurrency League",
            slug=f"concurrency-{suffix}",
            normalized_slug=f"concurrency-{suffix}",
            timezone="America/Chicago",
            status=InstallationStatus.ACTIVE,
            registration_status=WorkspaceRegistrationStatus.ACTIVE,
            onboarding_stage=OnboardingStage.COMPLETE,
        )
        session.add(workspace)
        session.flush()

        administrator = AuthenticationRepository(session).create_user(
            username=f"internal-admin-{suffix}",
            display_name="Concurrency Administrator",
            email=email,
            password=ADMIN_PASSWORD,
            role=UserRole.ADMIN,
            workspace_id=workspace.id,
        )
        administrator.email_verified_at = datetime.now(UTC)
        session.commit()

        administrator_id = administrator.id
        workspace_id = workspace.id

    yield administrator_id, email

    delete_workspaces([workspace_id])


def active_session_count(user_id: int) -> int:
    with SessionFactory() as session:
        return len(
            session.scalars(
                select(AuthSession.id).where(
                    AuthSession.user_id == user_id,
                    AuthSession.revoked_at.is_(None),
                )
            ).all()
        )


def test_concurrent_email_logins_each_keep_their_own_active_session(
    active_administrator: tuple[int, str],
) -> None:
    administrator_id, email = active_administrator

    def log_in(_: int) -> object:
        with SessionFactory() as session:
            result = log_in_by_email(
                session,
                email=email,
                password=ADMIN_PASSWORD,
                session_lifetime_hours=8,
            )
            session.commit()

            return result.auth_session.id

    for round_index in range(5):
        results = run_concurrently(log_in, 4)

        assert all(isinstance(result, int) for result in results), results
        assert len(set(results)) == 4
        # Email login no longer replaces sessions: every login stays active.
        assert active_session_count(administrator_id) == 4 * (round_index + 1)


def test_concurrent_session_replacement_leaves_one_active_session(
    active_administrator: tuple[int, str],
) -> None:
    administrator_id, _ = active_administrator

    def replace(_: int) -> object:
        with SessionFactory() as session:
            user = session.get(User, administrator_id)
            assert user is not None
            created = SessionRepository(session).replace_user_sessions(
                user=user,
                lifetime_hours=8,
            )
            session.commit()

            return created.session.id

    for _ in range(5):
        results = run_concurrently(replace, 6)

        assert all(isinstance(result, int) for result in results), results
        assert active_session_count(administrator_id) == 1


def test_concurrent_same_email_registration_permits_one_identity() -> None:
    email = f"concurrent-email-{uuid4().hex[:12]}@example.com"

    def register(_: int) -> object:
        with SessionFactory() as session:
            result = register_personal_account(
                session,
                registration_mode=RegistrationMode.OPEN,
                display_name="Concurrent Account",
                email=email,
                password=ADMIN_PASSWORD,
            )
            session.commit()

            return result.user.id

    try:
        results = run_concurrently(register, 3)

        successes = [result for result in results if isinstance(result, int)]
        duplicates = [
            result for result in results if isinstance(result, DuplicateAccountEmailError)
        ]

        assert len(successes) == 1, results
        assert len(duplicates) == 2, results

        with SessionFactory() as session:
            users = session.scalars(select(User).where(User.normalized_email == email)).all()
            tokens = session.scalars(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.user_id.in_([user.id for user in users])
                )
            ).all()
            deliveries = identity_deliveries(session, users[0].id)
            jobs = email_jobs_for(session, [delivery.id for delivery in deliveries])

        assert len(users) == 1
        # Registration queues one workspace-free delivery and creates no token.
        assert len(tokens) == 0
        assert len(deliveries) == 1
        assert deliveries[0].workspace_id is None
        assert len(jobs) == 1
    finally:
        with SessionFactory() as session:
            delete_users(
                session,
                list(session.scalars(select(User.id).where(User.normalized_email == email))),
            )
            session.commit()


@pytest.mark.parametrize(
    "token_type",
    ["email_verification", "password_reset"],
)
def test_concurrent_redemption_of_one_token_permits_one_success(
    active_administrator: tuple[int, str],
    token_type: str,
) -> None:
    administrator_id, _ = active_administrator
    raw_token = f"concurrent-token-{uuid4().hex}"

    with SessionFactory() as session:
        repository = AccountTokenRepository(session)
        create = (
            repository.create_email_verification_token
            if token_type == "email_verification"
            else repository.create_password_reset_token
        )
        create(
            user_id=administrator_id,
            raw_token=raw_token,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.commit()

    def redeem(_: int) -> object:
        with SessionFactory() as session:
            repository = AccountTokenRepository(session)
            redeem_token = (
                repository.redeem_email_verification_token
                if token_type == "email_verification"
                else repository.redeem_password_reset_token
            )
            token = redeem_token(raw_token)
            session.commit()

            return token is not None

    results = run_concurrently(redeem, 4)

    assert sorted(results) == [False, False, False, True], results


def test_invalid_replacement_password_does_not_consume_reset_token(
    active_administrator: tuple[int, str],
) -> None:
    administrator_id, _ = active_administrator

    with SessionFactory() as session:
        administrator = session.get(User, administrator_id)
        assert administrator is not None
        issued = issue_password_reset_token(
            session,
            user=administrator,
            lifetime_minutes=30,
        )
        session.commit()

    with SessionFactory() as session, pytest.raises(InvalidPasswordError):
        # The route does not commit on this error; leaving the context
        # rolls the transaction back exactly as the route does.
        reset_password(
            session,
            raw_token=issued.raw_token,
            new_password="short",
        )

    with SessionFactory() as session:
        stored = session.scalar(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == hash_token(issued.raw_token)
            )
        )
        assert stored is not None
        assert stored.used_at is None

    replacement = "a completely new passphrase"

    with SessionFactory() as session:
        completed = reset_password(
            session,
            raw_token=issued.raw_token,
            new_password=replacement,
        )
        session.commit()

        assert verify_password(replacement, completed.user.password_hash)


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


@pytest.mark.parametrize(
    ("model", "constraint_name"),
    [
        (EmailVerificationToken, "uq_email_verification_tokens_token_hash"),
        (PasswordResetToken, "uq_password_reset_tokens_token_hash"),
    ],
)
def test_duplicate_token_hash_is_rejected_by_named_constraint(
    database_session: Session,
    active_administrator: tuple[int, str],
    model: type[EmailVerificationToken] | type[PasswordResetToken],
    constraint_name: str,
) -> None:
    administrator_id, _ = active_administrator
    token_hash = hash_token(f"duplicate-{uuid4().hex}")
    expires_at = datetime.now(UTC) + timedelta(hours=1)

    database_session.add(
        model(
            user_id=administrator_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )
    database_session.flush()

    database_session.add(
        model(
            user_id=administrator_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )

    with pytest.raises(IntegrityError) as exc_info:
        database_session.flush()

    assert exc_info.value.orig.diag.constraint_name == constraint_name
