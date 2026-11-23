"""FR-20 deletion races on real PostgreSQL connections.

Every worker uses its own SessionFactory session, so each one is a
separate PostgreSQL connection and transaction.
"""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.auth.authentication import (
    InvalidCredentialsError,
    log_in_administrator_by_email,
    log_in_workspace_spectator,
)
from app.db.auth_models import (
    AuthSession,
    AuthSessionPurpose,
    LoginAttempt,
    User,
    UserRole,
)
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation
from app.db.session import SessionFactory
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.session_repository import SessionRepository
from app.services.workspace_deletion import (
    WorkspaceDeletionUnavailableError,
    cancel_workspace_deletion,
    request_workspace_deletion,
)

ADMIN_PASSWORD = "correct horse battery staple"
SPECTATOR_PASSWORD = "shared spectator password 2026"


@dataclass(frozen=True, slots=True)
class Seeded:
    workspace_id: int
    slug: str
    administrator_id: int
    administrator_email: str


def run_concurrently(worker: Callable[[int], object], count: int) -> list[object]:
    barrier = threading.Barrier(count)

    def synchronized(index: int) -> object:
        barrier.wait(timeout=10)

        try:
            return worker(index)
        except Exception as exc:  # noqa: BLE001 - results are asserted
            return exc

    with ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(synchronized, range(count)))


@pytest.fixture(autouse=True)
def isolate_login_attempts() -> Iterator[None]:
    with SessionFactory() as session:
        existing = set(session.scalars(select(LoginAttempt.id)).all())

    yield

    with SessionFactory() as session:
        cleanup = delete(LoginAttempt)

        if existing:
            cleanup = cleanup.where(LoginAttempt.id.not_in(existing))

        session.execute(cleanup)
        session.commit()


@pytest.fixture
def seed() -> Iterator[Callable[[], Seeded]]:
    created: list[int] = []

    def create() -> Seeded:
        suffix = uuid4().hex[:12]
        slug = f"deletion-race-{suffix}"
        email = f"deletion-race-{suffix}@example.com"

        with SessionFactory() as session:
            workspace = Installation(
                application_name="Deletion Race League",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=InstallationStatus.ACTIVE,
                registration_status=WorkspaceRegistrationStatus.ACTIVE,
                onboarding_stage=OnboardingStage.COMPLETE,
            )
            session.add(workspace)
            session.flush()
            repository = AuthenticationRepository(session)
            administrator = repository.create_user(
                username=f"race-admin-{suffix}",
                display_name="Race Administrator",
                email=email,
                password=ADMIN_PASSWORD,
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC)
            repository.create_user(
                username=f"race-spectator-{suffix}",
                display_name="Race Spectator",
                password=SPECTATOR_PASSWORD,
                role=UserRole.SPECTATOR,
                workspace_id=workspace.id,
            )
            session.commit()
            created.append(workspace.id)

            return Seeded(workspace.id, slug, administrator.id, email)

    yield create

    with SessionFactory() as session:
        for workspace_id in created:
            for user in session.scalars(select(User).where(User.workspace_id == workspace_id)):
                session.delete(user)
            session.flush()
            session.execute(delete(BackgroundJob).where(BackgroundJob.workspace_id == workspace_id))
            workspace = session.get(Installation, workspace_id)
            if workspace is not None:
                session.delete(workspace)
        session.commit()


def standard_session_id(seeded: Seeded) -> int:
    with SessionFactory() as session:
        user = session.get(User, seeded.administrator_id)
        assert user is not None
        created = SessionRepository(session).create_session(user=user, lifetime_hours=8)
        session.commit()

        return created.session.id


def active_sessions(workspace_id: int) -> list[AuthSession]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(AuthSession)
                .join(User, User.id == AuthSession.user_id)
                .where(
                    User.workspace_id == workspace_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > datetime.now(UTC),
                )
            ).all()
        )
        for row in rows:
            session.expunge(row)

        return rows


def deletion_jobs(workspace_id: int) -> list[BackgroundJob]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.workspace_id == workspace_id,
                    BackgroundJob.job_type == BackgroundJobType.WORKSPACE_DELETION,
                )
            ).all()
        )
        for row in rows:
            session.expunge(row)

        return rows


def load_workspace(workspace_id: int) -> Installation:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)
        assert workspace is not None
        session.expunge(workspace)

        return workspace


def request(seeded: Seeded, auth_session_id: int):
    with SessionFactory() as session:
        state = request_workspace_deletion(
            session,
            workspace_id=seeded.workspace_id,
            user_id=seeded.administrator_id,
            auth_session_id=auth_session_id,
            current_password=ADMIN_PASSWORD,
            confirmation_slug=seeded.slug,
        )
        session.commit()

        return state


def test_concurrent_requests_create_one_job_and_one_schedule(
    seed: Callable[[], Seeded],
) -> None:
    seeded = seed()
    session_id = standard_session_id(seeded)

    results = run_concurrently(lambda _: request(seeded, session_id), 4)

    assert not [result for result in results if isinstance(result, Exception)]
    workspace = load_workspace(seeded.workspace_id)
    assert {result.scheduled_for for result in results} == {workspace.deletion_scheduled_for}

    jobs = deletion_jobs(seeded.workspace_id)
    assert len(jobs) == 1
    assert jobs[0].status is BackgroundJobStatus.PENDING


def test_repeated_request_never_extends_schedule(seed: Callable[[], Seeded]) -> None:
    seeded = seed()
    session_id = standard_session_id(seeded)
    first = request(seeded, session_id)

    with SessionFactory() as session:
        repeated = request_workspace_deletion(
            session,
            workspace_id=seeded.workspace_id,
            user_id=seeded.administrator_id,
            auth_session_id=session_id,
            current_password=ADMIN_PASSWORD,
            confirmation_slug=seeded.slug,
            now=datetime.now(UTC) + timedelta(hours=1),
        )
        session.commit()

    assert repeated.scheduled_for == first.scheduled_for
    assert len(deletion_jobs(seeded.workspace_id)) == 1


@pytest.mark.parametrize("round_number", range(4))
def test_admin_login_racing_request_leaves_no_standard_session(
    seed: Callable[[], Seeded],
    round_number: int,
) -> None:
    del round_number
    seeded = seed()
    requester_session = standard_session_id(seeded)

    def worker(index: int) -> object:
        if index == 0:
            return request(seeded, requester_session)

        with SessionFactory() as session:
            result = log_in_administrator_by_email(
                session,
                email=seeded.administrator_email,
                password=ADMIN_PASSWORD,
                session_lifetime_hours=8,
            )
            session.commit()

            return result.session_purpose

    results = run_concurrently(worker, 2)
    workspace = load_workspace(seeded.workspace_id)
    remaining = active_sessions(seeded.workspace_id)

    if workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING:
        assert all(item.purpose is AuthSessionPurpose.DELETION_RECOVERY for item in remaining)
    else:
        # The login replaced the requester's session first, so the request
        # was refused and nothing changed.
        assert isinstance(results[0], WorkspaceDeletionUnavailableError)
        assert deletion_jobs(seeded.workspace_id) == []


@pytest.mark.parametrize("round_number", range(4))
def test_spectator_login_racing_request_leaves_no_spectator_session(
    seed: Callable[[], Seeded],
    round_number: int,
) -> None:
    del round_number
    seeded = seed()
    requester_session = standard_session_id(seeded)

    def worker(index: int) -> object:
        if index == 0:
            return request(seeded, requester_session)

        with SessionFactory() as session:
            log_in_workspace_spectator(
                session,
                workspace_slug=seeded.slug,
                password=SPECTATOR_PASSWORD,
                session_lifetime_hours=8,
                browser_id="race-browser",
            )
            session.commit()

            return "spectator"

    results = run_concurrently(worker, 2)

    assert not isinstance(results[0], Exception)
    assert results[1] == "spectator" or isinstance(results[1], InvalidCredentialsError)
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.DELETION_PENDING
    )
    assert active_sessions(seeded.workspace_id) == []


def recovery_session_id(seeded: Seeded) -> int:
    with SessionFactory() as session:
        result = log_in_administrator_by_email(
            session,
            email=seeded.administrator_email,
            password=ADMIN_PASSWORD,
            session_lifetime_hours=8,
        )
        session.commit()

        assert result.session_purpose is AuthSessionPurpose.DELETION_RECOVERY
        return result.auth_session.id


def test_concurrent_cancellations_succeed_once(seed: Callable[[], Seeded]) -> None:
    seeded = seed()
    request(seeded, standard_session_id(seeded))
    recovery_id = recovery_session_id(seeded)

    def worker(_: int) -> object:
        with SessionFactory() as session:
            cancel_workspace_deletion(
                session,
                workspace_id=seeded.workspace_id,
                user_id=seeded.administrator_id,
                auth_session_id=recovery_id,
                current_password=ADMIN_PASSWORD,
            )
            session.commit()

            return "canceled"

    results = run_concurrently(worker, 3)

    assert results.count("canceled") == 1
    assert all(
        isinstance(result, WorkspaceDeletionUnavailableError)
        for result in results
        if result != "canceled"
    )
    workspace = load_workspace(seeded.workspace_id)
    assert workspace.registration_status is WorkspaceRegistrationStatus.ACTIVE
    assert [job.status for job in deletion_jobs(seeded.workspace_id)] == [
        BackgroundJobStatus.CANCELED
    ]
    assert active_sessions(seeded.workspace_id) == []


def test_cancellation_at_exact_cutoff_loses(seed: Callable[[], Seeded]) -> None:
    seeded = seed()
    request(seeded, standard_session_id(seeded))
    recovery_id = recovery_session_id(seeded)
    scheduled_for = load_workspace(seeded.workspace_id).deletion_scheduled_for
    assert scheduled_for is not None

    with SessionFactory() as session, pytest.raises(WorkspaceDeletionUnavailableError):
        cancel_workspace_deletion(
            session,
            workspace_id=seeded.workspace_id,
            user_id=seeded.administrator_id,
            auth_session_id=recovery_id,
            current_password=ADMIN_PASSWORD,
            now=scheduled_for,
        )

    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.DELETION_PENDING
    )
    assert [job.status for job in deletion_jobs(seeded.workspace_id)] == [
        BackgroundJobStatus.PENDING
    ]


def test_due_deletion_job_is_not_claimed_before_its_handler_exists(
    seed: Callable[[], Seeded],
) -> None:
    seeded = seed()
    request(seeded, standard_session_id(seeded))
    job = deletion_jobs(seeded.workspace_id)[0]
    after_cutoff = job.available_at + timedelta(seconds=1)

    with SessionFactory() as session:
        claimed = BackgroundJobRepository(session).claim_next_available(
            worker_id="deletion-test-worker",
            lease_duration=timedelta(minutes=5),
            now=after_cutoff,
        )

        assert claimed is None or claimed.id != job.id
        session.rollback()

    assert deletion_jobs(seeded.workspace_id)[0].status is BackgroundJobStatus.PENDING
