"""FR-20 deletion request, deletion-recovery sessions, and cancellation."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.api.auth_dependencies import (
    get_current_user,
    resolve_session_authentication_context,
)
from app.db.auth_models import (
    AuthSession,
    AuthSessionPurpose,
    LoginAttempt,
    PasswordResetToken,
    RegistrationRateLimit,
    SessionRevocationReason,
    User,
    UserRole,
)
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    WorkspaceDeletionRecord,
)
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import AuthenticationRepository
from app.services.password_recovery import reset_administrator_password
from tests.support.identity_email import (
    PASSWORD_RESET,
    deliver_and_extract_token,
    identity_deliveries,
    issue_password_reset_token,
)

TRUSTED_ORIGIN = "http://localhost:5173"
ADMIN_PASSWORD = "correct horse battery staple"
SPECTATOR_PASSWORD = "shared spectator password 2026"
DELETION_PATH = "/api/admin/workspace/deletion"
CANCELLATION_PATH = "/api/admin/workspace/deletion/cancellation"
RECOVERY_ALLOWLIST = {
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"),
    ("GET", DELETION_PATH),
    ("POST", CANCELLATION_PATH),
}
FORBIDDEN_RESPONSE_FRAGMENTS = (
    "password_hash",
    "username",
    "internal-",
    "session_token",
    "encrypted_credentials",
    "payload",
    "provider_league_id",
    "$argon2",
)


@dataclass(frozen=True, slots=True)
class SeededWorkspace:
    workspace_id: int
    slug: str
    administrator_id: int
    administrator_email: str
    spectator_id: int


@pytest.fixture(autouse=True)
def isolate_throttle_rows() -> Iterator[None]:
    with SessionFactory() as session:
        existing_attempts = set(session.scalars(select(LoginAttempt.id)).all())
        existing_limits = set(session.scalars(select(RegistrationRateLimit.id)).all())

    try:
        yield
    finally:
        with SessionFactory() as session:
            attempts = delete(LoginAttempt)
            limits = delete(RegistrationRateLimit)

            if existing_attempts:
                attempts = attempts.where(LoginAttempt.id.not_in(existing_attempts))

            if existing_limits:
                limits = limits.where(RegistrationRateLimit.id.not_in(existing_limits))

            session.execute(attempts)
            session.execute(limits)
            session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    inherited = app.dependency_overrides.pop(get_current_user, None)

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        if inherited is not None:
            app.dependency_overrides[get_current_user] = inherited


def _seed_workspace() -> SeededWorkspace:
    suffix = uuid4().hex[:12]
    slug = f"deletion-{suffix}"
    email = f"deletion-admin-{suffix}@example.com"

    with SessionFactory() as session:
        workspace = Installation(
            application_name="Deletion League",
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
            username=f"internal-admin-{suffix}",
            display_name="Deletion Administrator",
            email=email,
            password=ADMIN_PASSWORD,
            role=UserRole.ADMIN,
            workspace_id=workspace.id,
        )
        administrator.email_verified_at = datetime.now(UTC)
        spectator = repository.create_user(
            username=f"internal-spectator-{suffix}",
            display_name="Deletion Spectator",
            password=SPECTATOR_PASSWORD,
            role=UserRole.SPECTATOR,
            workspace_id=workspace.id,
        )
        session.add(
            ProviderConnection(
                installation_id=workspace.id,
                provider=FantasyProvider.ESPN,
                display_name="Retained connection",
                encrypted_credentials=None,
                credential_version=None,
                is_active=True,
            )
        )
        session.commit()

        return SeededWorkspace(
            workspace_id=workspace.id,
            slug=slug,
            administrator_id=administrator.id,
            administrator_email=email,
            spectator_id=spectator.id,
        )


def _delete_workspace(workspace_id: int) -> None:
    with SessionFactory() as session:
        for user in session.scalars(select(User).where(User.workspace_id == workspace_id)):
            session.delete(user)

        session.flush()
        session.execute(
            delete(ProviderConnection).where(ProviderConnection.installation_id == workspace_id)
        )
        session.execute(delete(BackgroundJob).where(BackgroundJob.workspace_id == workspace_id))
        workspace = session.get(Installation, workspace_id)

        if workspace is not None:
            session.delete(workspace)

        session.commit()


@pytest.fixture
def seeded() -> Iterator[SeededWorkspace]:
    workspace = _seed_workspace()

    yield workspace

    _delete_workspace(workspace.workspace_id)


@pytest.fixture
def other_workspace() -> Iterator[SeededWorkspace]:
    workspace = _seed_workspace()

    yield workspace

    _delete_workspace(workspace.workspace_id)


def _pre_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/api/auth/csrf")
    assert response.status_code == status.HTTP_200_OK

    return {"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def admin_login(client: TestClient, email: str, password: str = ADMIN_PASSWORD):
    return client.post(
        "/api/auth/admin/login",
        headers=_pre_auth_headers(client),
        json={"email": email, "password": password},
    )


def spectator_login(client: TestClient, slug: str):
    return client.post(
        "/api/auth/spectator/login",
        headers=_pre_auth_headers(client),
        json={"workspace_slug": slug, "password": SPECTATOR_PASSWORD},
    )


def session_headers(csrf_token: str) -> dict[str, str]:
    return {"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token}


def standard_login(client: TestClient, seeded: SeededWorkspace) -> str:
    response = admin_login(client, seeded.administrator_email)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["session_purpose"] == "standard"

    return response.json()["csrf_token"]


def request_deletion(
    client: TestClient,
    csrf_token: str,
    *,
    slug: str,
    password: str = ADMIN_PASSWORD,
):
    return client.post(
        DELETION_PATH,
        headers=session_headers(csrf_token),
        json={"current_password": password, "confirmation_slug": slug},
    )


def put_in_deletion_pending(client: TestClient, seeded: SeededWorkspace) -> None:
    csrf_token = standard_login(client, seeded)
    response = request_deletion(client, csrf_token, slug=seeded.slug)
    assert response.status_code == status.HTTP_202_ACCEPTED


def recovery_login(client: TestClient, seeded: SeededWorkspace) -> str:
    response = admin_login(client, seeded.administrator_email)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["session_purpose"] == "deletion_recovery"

    return response.json()["csrf_token"]


def load_workspace(workspace_id: int) -> Installation:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)
        assert workspace is not None
        session.expunge(workspace)

        return workspace


def workspace_sessions(workspace_id: int) -> list[AuthSession]:
    with SessionFactory() as session:
        sessions = list(
            session.scalars(
                select(AuthSession)
                .join(User, User.id == AuthSession.user_id)
                .where(User.workspace_id == workspace_id)
                .order_by(AuthSession.id)
            ).all()
        )

        for item in sessions:
            session.expunge(item)

        return sessions


def workspace_jobs(workspace_id: int) -> list[BackgroundJob]:
    with SessionFactory() as session:
        jobs = list(
            session.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.workspace_id == workspace_id)
                .order_by(BackgroundJob.id)
            ).all()
        )

        for job in jobs:
            session.expunge(job)

        return jobs


def add_job(
    workspace_id: int,
    job_status: BackgroundJobStatus,
    job_type: BackgroundJobType = BackgroundJobType.INCREMENTAL_SEASON_SYNC,
) -> int:
    now = datetime.now(UTC)
    values: dict[str, object] = {}

    if job_status is BackgroundJobStatus.RUNNING:
        values = {
            "claimed_at": now,
            "lease_expires_at": now + timedelta(minutes=5),
            "claimed_by": "test-worker",
            "started_at": now,
            "attempt_count": 1,
        }
    elif job_status is BackgroundJobStatus.SUCCEEDED:
        values = {"completed_at": now, "attempt_count": 1}

    with SessionFactory() as session:
        job = BackgroundJob(
            workspace_id=workspace_id,
            job_type=job_type,
            status=job_status,
            payload={"season_id": 1},
            available_at=now,
            **values,
        )
        session.add(job)
        session.commit()

        return job.id


def assert_safe(body: object) -> None:
    text = str(body)

    for fragment in FORBIDDEN_RESPONSE_FRAGMENTS:
        assert fragment not in text


# Deletion status ---------------------------------------------------------


def test_status_for_standard_session_on_active_workspace(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    standard_login(client, seeded)

    response = client.get(DELETION_PATH)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "active",
        "grace_period_days": 30,
        "confirmation_slug": seeded.slug,
        "requested_at": None,
        "scheduled_for": None,
        "cancellable": None,
    }
    assert_safe(response.json())


def test_status_for_recovery_session(client: TestClient, seeded: SeededWorkspace) -> None:
    put_in_deletion_pending(client, seeded)
    recovery_login(client, seeded)

    response = client.get(DELETION_PATH)
    workspace = load_workspace(seeded.workspace_id)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["status"] == "deletion_pending"
    assert body["cancellable"] is True
    assert body["confirmation_slug"] is None
    assert body["grace_period_days"] is None
    assert datetime.fromisoformat(body["requested_at"]) == workspace.deletion_requested_at
    assert datetime.fromisoformat(body["scheduled_for"]) == workspace.deletion_scheduled_for
    assert_safe(body)


def test_status_requires_administrator(client: TestClient, seeded: SeededWorkspace) -> None:
    assert spectator_login(client, seeded.slug).status_code == status.HTTP_200_OK

    response = client.get(DELETION_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_status_requires_authentication(client: TestClient) -> None:
    assert client.get(DELETION_PATH).status_code == status.HTTP_401_UNAUTHORIZED


# Deletion request -----------------------------------------------------------


def test_request_moves_workspace_to_deletion_pending(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    spectator_client = TestClient(app)
    assert spectator_login(spectator_client, seeded.slug).status_code == status.HTTP_200_OK
    csrf_token = standard_login(client, seeded)

    pending_id = add_job(seeded.workspace_id, BackgroundJobStatus.PENDING)
    retry_id = add_job(seeded.workspace_id, BackgroundJobStatus.RETRY_SCHEDULED)
    running_id = add_job(seeded.workspace_id, BackgroundJobStatus.RUNNING)
    succeeded_id = add_job(seeded.workspace_id, BackgroundJobStatus.SUCCEEDED)

    before = datetime.now(UTC)
    response = request_deletion(client, csrf_token, slug=f"  {seeded.slug.upper()} ")

    assert response.status_code == status.HTTP_202_ACCEPTED
    body = response.json()
    assert body["code"] == "workspace_deletion_requested"
    assert_safe(body)

    workspace = load_workspace(seeded.workspace_id)
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    assert workspace.status is InstallationStatus.ACTIVE
    assert workspace.deletion_requested_at is not None
    assert workspace.deletion_requested_at >= before
    assert workspace.deletion_scheduled_for == workspace.deletion_requested_at + timedelta(days=30)
    assert datetime.fromisoformat(body["scheduled_for"]) == workspace.deletion_scheduled_for

    # Slug, email, and connection are retained.
    assert workspace.slug == seeded.slug
    with SessionFactory() as session:
        administrator = session.get(User, seeded.administrator_id)
        assert administrator is not None
        assert administrator.email == seeded.administrator_email
        assert session.scalar(
            select(ProviderConnection.id).where(
                ProviderConnection.installation_id == seeded.workspace_id
            )
        )
        # No deletion record or destructive cleanup happens at request time.
        assert (
            session.scalar(
                select(WorkspaceDeletionRecord.id).where(
                    WorkspaceDeletionRecord.former_workspace_id == seeded.workspace_id
                )
            )
            is None
        )

    # Every administrator and spectator session is revoked.
    sessions = workspace_sessions(seeded.workspace_id)
    assert {item.user_id for item in sessions} == {
        seeded.administrator_id,
        seeded.spectator_id,
    }
    assert all(item.revoked_at is not None for item in sessions)
    assert all(
        item.revocation_reason is SessionRevocationReason.WORKSPACE_DELETION_REQUESTED
        for item in sessions
    )

    # Queued jobs are canceled; running and finished jobs are untouched.
    jobs = {job.id: job for job in workspace_jobs(seeded.workspace_id)}
    assert jobs[pending_id].status is BackgroundJobStatus.CANCELED
    assert jobs[retry_id].status is BackgroundJobStatus.CANCELED
    assert jobs[running_id].status is BackgroundJobStatus.RUNNING
    assert jobs[running_id].claimed_by == "test-worker"
    assert jobs[succeeded_id].status is BackgroundJobStatus.SUCCEEDED

    deletion_jobs = [
        job for job in jobs.values() if job.job_type is BackgroundJobType.WORKSPACE_DELETION
    ]
    assert len(deletion_jobs) == 1
    assert deletion_jobs[0].status is BackgroundJobStatus.PENDING
    assert deletion_jobs[0].available_at == workspace.deletion_scheduled_for
    assert deletion_jobs[0].idempotency_key == f"workspace_deletion:{seeded.workspace_id}"
    assert deletion_jobs[0].payload == {}

    # Cookies are cleared and old sessions no longer authenticate.
    assert "fantasy_session" not in client.cookies
    assert client.get("/api/auth/me").status_code == status.HTTP_401_UNAUTHORIZED
    assert spectator_client.get("/api/auth/me").status_code == status.HTTP_401_UNAUTHORIZED


def test_request_rejects_wrong_slug(client: TestClient, seeded: SeededWorkspace) -> None:
    csrf_token = standard_login(client, seeded)

    response = request_deletion(client, csrf_token, slug=f"{seeded.slug}-other")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "workspace_slug_mismatch"
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.ACTIVE
    )
    assert workspace_jobs(seeded.workspace_id) == []


def test_request_rejects_wrong_password(client: TestClient, seeded: SeededWorkspace) -> None:
    csrf_token = standard_login(client, seeded)

    response = request_deletion(client, csrf_token, slug=seeded.slug, password="wrong password")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "invalid_current_password"
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.ACTIVE
    )
    assert client.get("/api/auth/me").status_code == status.HTTP_200_OK


def test_request_requires_session_csrf_and_origin(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    csrf_token = standard_login(client, seeded)
    body = {"current_password": ADMIN_PASSWORD, "confirmation_slug": seeded.slug}

    missing_csrf = client.post(DELETION_PATH, headers={"Origin": TRUSTED_ORIGIN}, json=body)
    bad_origin = client.post(
        DELETION_PATH,
        headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf_token},
        json=body,
    )

    assert missing_csrf.status_code == status.HTTP_403_FORBIDDEN
    assert bad_origin.status_code == status.HTTP_403_FORBIDDEN
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.ACTIVE
    )


def test_request_requires_administrator(client: TestClient, seeded: SeededWorkspace) -> None:
    login = spectator_login(client, seeded.slug)
    assert login.status_code == status.HTTP_200_OK

    response = request_deletion(client, login.json()["csrf_token"], slug=seeded.slug)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.ACTIVE
    )


@pytest.mark.parametrize(
    "registration_status",
    [WorkspaceRegistrationStatus.SUSPENDED],
)
def test_request_refused_for_non_active_workspace(
    client: TestClient,
    seeded: SeededWorkspace,
    registration_status: WorkspaceRegistrationStatus,
) -> None:
    csrf_token = standard_login(client, seeded)

    with SessionFactory() as session:
        session.execute(
            update(Installation)
            .where(Installation.id == seeded.workspace_id)
            .values(registration_status=registration_status)
        )
        session.commit()

    response = request_deletion(client, csrf_token, slug=seeded.slug)

    assert response.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_409_CONFLICT)
    assert workspace_jobs(seeded.workspace_id) == []


def test_request_is_rate_limited(client: TestClient, seeded: SeededWorkspace) -> None:
    csrf_token = standard_login(client, seeded)

    for _ in range(5):
        response = request_deletion(client, csrf_token, slug=seeded.slug, password="wrong")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    throttled = request_deletion(client, csrf_token, slug=seeded.slug)

    assert throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert int(throttled.headers["Retry-After"]) > 0
    assert throttled.json()["code"] == "workspace_deletion_throttled"
    # The correct password was not even evaluated: nothing changed.
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.ACTIVE
    )

    with SessionFactory() as session:
        rows = session.scalars(
            select(RegistrationRateLimit).where(
                RegistrationRateLimit.action == "workspace_deletion_request"
            )
        ).all()
        assert rows
        for row in rows:
            assert ADMIN_PASSWORD not in row.identity_hash
            assert seeded.slug not in row.identity_hash


def test_recovery_session_cannot_request_deletion(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)
    csrf_token = recovery_login(client, seeded)
    scheduled_for = load_workspace(seeded.workspace_id).deletion_scheduled_for

    response = request_deletion(client, csrf_token, slug=seeded.slug)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "session_restricted"
    assert load_workspace(seeded.workspace_id).deletion_scheduled_for == scheduled_for


# Deletion-pending login -------------------------------------------------------


def test_recovery_login_creates_restricted_fifteen_minute_session(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)

    response = admin_login(client, seeded.administrator_email)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["session_purpose"] == "deletion_recovery"
    assert_safe(response.json()["user"])

    recovery = [
        item
        for item in workspace_sessions(seeded.workspace_id)
        if item.purpose is AuthSessionPurpose.DELETION_RECOVERY
    ]
    assert len(recovery) == 1
    assert recovery[0].expires_at - recovery[0].created_at == timedelta(minutes=15)

    me = client.get("/api/auth/me")
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["session_purpose"] == "deletion_recovery"
    assert me.json()["workspace"]["workspace_access"] == "inactive"
    assert me.json()["workspace"]["registration_status"] is None
    assert_safe(me.json())

    # Access never renews or extends the session.
    client.get(DELETION_PATH)
    after = [
        item
        for item in workspace_sessions(seeded.workspace_id)
        if item.purpose is AuthSessionPurpose.DELETION_RECOVERY
    ]
    assert after[0].expires_at == recovery[0].expires_at


def test_recovery_session_expires(client: TestClient, seeded: SeededWorkspace) -> None:
    put_in_deletion_pending(client, seeded)
    recovery_login(client, seeded)

    with SessionFactory() as session:
        session.execute(
            update(AuthSession)
            .where(AuthSession.purpose == AuthSessionPurpose.DELETION_RECOVERY)
            .where(AuthSession.user_id == seeded.administrator_id)
            .values(
                created_at=datetime.now(UTC) - timedelta(minutes=16),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        session.commit()

    assert client.get("/api/auth/me").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get(DELETION_PATH).status_code == status.HTTP_401_UNAUTHORIZED


def test_second_recovery_login_replaces_first(client: TestClient, seeded: SeededWorkspace) -> None:
    put_in_deletion_pending(client, seeded)
    recovery_login(client, seeded)
    second_client = TestClient(app)
    recovery_login(second_client, seeded)

    active = [
        item
        for item in workspace_sessions(seeded.workspace_id)
        if item.revoked_at is None and item.expires_at > datetime.now(UTC)
    ]
    assert len(active) == 1
    assert active[0].purpose is AuthSessionPurpose.DELETION_RECOVERY
    assert client.get("/api/auth/me").status_code == status.HTTP_401_UNAUTHORIZED
    assert second_client.get("/api/auth/me").status_code == status.HTTP_200_OK


def test_wrong_credentials_remain_generic_while_pending(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)

    wrong_password = admin_login(client, seeded.administrator_email, "wrong password")
    unknown = admin_login(client, f"missing-{uuid4().hex}@example.com", "wrong password")

    assert wrong_password.status_code == status.HTTP_401_UNAUTHORIZED
    assert wrong_password.json() == unknown.json() == {"detail": "Invalid email or password."}


def test_spectator_login_is_generic_while_pending(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)

    response = spectator_login(client, seeded.slug)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": "The workspace or spectator credentials are incorrect."}


def test_no_recovery_session_at_or_after_cutoff(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)
    now = datetime.now(UTC)

    with SessionFactory() as session:
        session.execute(
            update(Installation)
            .where(Installation.id == seeded.workspace_id)
            .values(
                deletion_requested_at=now - timedelta(days=31),
                deletion_scheduled_for=now - timedelta(seconds=1),
            )
        )
        session.commit()

    response = admin_login(client, seeded.administrator_email)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": "Invalid email or password."}


@pytest.mark.parametrize(
    ("registration_status", "installation_status", "stage"),
    [
        (WorkspaceRegistrationStatus.SUSPENDED, InstallationStatus.ACTIVE, None),
        (
            WorkspaceRegistrationStatus.PROVISIONAL,
            InstallationStatus.SETUP_IN_PROGRESS,
            OnboardingStage.EMAIL_VERIFICATION_PENDING,
        ),
        (WorkspaceRegistrationStatus.DELETED, InstallationStatus.ACTIVE, None),
    ],
)
def test_inactive_states_never_issue_recovery_sessions(
    client: TestClient,
    seeded: SeededWorkspace,
    registration_status: WorkspaceRegistrationStatus,
    installation_status: InstallationStatus,
    stage: OnboardingStage | None,
) -> None:
    with SessionFactory() as session:
        session.execute(
            update(Installation)
            .where(Installation.id == seeded.workspace_id)
            .values(
                registration_status=registration_status,
                status=installation_status,
                onboarding_stage=stage,
            )
        )
        session.commit()

    response = admin_login(client, seeded.administrator_email)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert not [
        item
        for item in workspace_sessions(seeded.workspace_id)
        if item.purpose is AuthSessionPurpose.DELETION_RECOVERY
    ]


# Default deny ---------------------------------------------------------------


def _depends_on(dependant, target) -> bool:
    return any(
        dependency.call is target or _depends_on(dependency, target)
        for dependency in dependant.dependencies
    )


def _application_routers():
    from app.api import (
        admin,
        analytics,
        auth,
        leagues,
        onboarding,
        registration,
        workspace_deletion,
    )

    return (
        auth.router,
        registration.router,
        onboarding.router,
        admin.router,
        workspace_deletion.router,
        leagues.router,
        analytics.router,
    )


def _authenticated_routes() -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []

    for router in _application_routers():
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue

            if not _depends_on(route.dependant, resolve_session_authentication_context):
                continue

            for method in sorted(route.methods):
                routes.append((method, route.path))

    return routes


def test_route_inventory_covers_every_family() -> None:
    paths = {path for _, path in _authenticated_routes()}

    for prefix in ("/api/admin/", "/api/leagues", "/api/analytics/", "/api/onboarding/"):
        assert any(path.startswith(prefix) for path in paths), prefix


def test_recovery_session_is_denied_everywhere_else(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)
    csrf_token = recovery_login(client, seeded)
    checked = 0

    for method, path in _authenticated_routes():
        if (method, path) in RECOVERY_ALLOWLIST:
            continue

        concrete = path.replace("{job_id}", "1").replace("{team_id}", "1")
        response = client.request(
            method,
            concrete,
            headers=session_headers(csrf_token),
            json={},
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, (method, path)
        assert response.json()["code"] == "session_restricted", (method, path)
        checked += 1

    assert checked >= 15
    # The recovery session itself is still intact afterwards.
    assert client.get("/api/auth/me").status_code == status.HTTP_200_OK


def test_recovery_session_can_log_out(client: TestClient, seeded: SeededWorkspace) -> None:
    put_in_deletion_pending(client, seeded)
    csrf_token = recovery_login(client, seeded)

    response = client.post("/api/auth/logout", headers=session_headers(csrf_token), json={})

    assert response.status_code == status.HTTP_200_OK
    assert client.get("/api/auth/me").status_code == status.HTTP_401_UNAUTHORIZED
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.DELETION_PENDING
    )


def test_recovery_session_cannot_reach_other_workspace(
    client: TestClient,
    seeded: SeededWorkspace,
    other_workspace: SeededWorkspace,
) -> None:
    put_in_deletion_pending(client, seeded)
    recovery_login(client, seeded)

    body = client.get(DELETION_PATH).json()
    me = client.get("/api/auth/me").json()

    assert me["workspace"]["workspace_id"] == seeded.workspace_id
    assert other_workspace.slug not in str(body) + str(me)
    assert load_workspace(other_workspace.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.ACTIVE
    )
    other_client = TestClient(app)
    assert spectator_login(other_client, other_workspace.slug).status_code == status.HTTP_200_OK


# Cancellation -------------------------------------------------------------------


def cancel(client: TestClient, csrf_token: str, password: str = ADMIN_PASSWORD):
    return client.post(
        CANCELLATION_PATH,
        headers=session_headers(csrf_token),
        json={"current_password": password},
    )


def test_cancellation_restores_active_workspace(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)
    revoked_before = {item.id for item in workspace_sessions(seeded.workspace_id)}
    csrf_token = recovery_login(client, seeded)

    response = cancel(client, csrf_token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "detail": "Deletion canceled. Sign in again to continue.",
        "code": "workspace_deletion_canceled",
    }
    workspace = load_workspace(seeded.workspace_id)
    assert workspace.registration_status is WorkspaceRegistrationStatus.ACTIVE
    assert workspace.deletion_requested_at is None
    assert workspace.deletion_scheduled_for is None

    deletion_jobs = [
        job
        for job in workspace_jobs(seeded.workspace_id)
        if job.job_type is BackgroundJobType.WORKSPACE_DELETION
    ]
    assert [job.status for job in deletion_jobs] == [BackgroundJobStatus.CANCELED]
    # No synchronization is enqueued by cancellation.
    assert all(
        job.job_type is BackgroundJobType.WORKSPACE_DELETION
        for job in workspace_jobs(seeded.workspace_id)
    )

    sessions = workspace_sessions(seeded.workspace_id)
    assert all(item.revoked_at is not None for item in sessions)
    assert all(
        item.revocation_reason is SessionRevocationReason.WORKSPACE_DELETION_REQUESTED
        for item in sessions
        if item.id in revoked_before
    )

    # Fresh standard login is required.
    assert client.get("/api/auth/me").status_code == status.HTTP_401_UNAUTHORIZED
    fresh = admin_login(client, seeded.administrator_email)
    assert fresh.json()["session_purpose"] == "standard"
    restored = client.get(DELETION_PATH)
    assert restored.status_code == status.HTTP_200_OK
    assert restored.json()["status"] == "active"


def test_cancellation_rejects_wrong_password(client: TestClient, seeded: SeededWorkspace) -> None:
    put_in_deletion_pending(client, seeded)
    csrf_token = recovery_login(client, seeded)

    response = cancel(client, csrf_token, "wrong password")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "invalid_current_password"
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.DELETION_PENDING
    )


def test_cancellation_is_rate_limited(client: TestClient, seeded: SeededWorkspace) -> None:
    put_in_deletion_pending(client, seeded)
    csrf_token = recovery_login(client, seeded)

    for _ in range(5):
        assert cancel(client, csrf_token, "wrong").status_code == status.HTTP_400_BAD_REQUEST

    throttled = cancel(client, csrf_token)

    assert throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert int(throttled.headers["Retry-After"]) > 0
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.DELETION_PENDING
    )


def test_cancellation_at_or_after_cutoff_fails_safely(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)
    csrf_token = recovery_login(client, seeded)
    now = datetime.now(UTC)

    with SessionFactory() as session:
        session.execute(
            update(Installation)
            .where(Installation.id == seeded.workspace_id)
            .values(
                deletion_requested_at=now - timedelta(days=30),
                deletion_scheduled_for=now,
            )
        )
        session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == seeded.administrator_id)
            .where(AuthSession.revoked_at.is_(None))
            .values(created_at=now - timedelta(minutes=1))
        )
        session.commit()

    status_response = client.get(DELETION_PATH)
    response = cancel(client, csrf_token)

    assert status_response.json()["cancellable"] is False
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "This workspace can no longer be restored.",
        "code": "workspace_deletion_unavailable",
        "retryable": False,
    }
    workspace = load_workspace(seeded.workspace_id)
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    deletion_jobs = [
        job
        for job in workspace_jobs(seeded.workspace_id)
        if job.job_type is BackgroundJobType.WORKSPACE_DELETION
    ]
    assert [job.status for job in deletion_jobs] == [BackgroundJobStatus.PENDING]


def test_recovery_session_older_than_request_cannot_cancel(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)
    csrf_token = recovery_login(client, seeded)
    requested_at = load_workspace(seeded.workspace_id).deletion_requested_at
    assert requested_at is not None

    with SessionFactory() as session:
        session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == seeded.administrator_id)
            .where(AuthSession.revoked_at.is_(None))
            .values(created_at=requested_at - timedelta(seconds=1))
        )
        session.commit()

    response = cancel(client, csrf_token)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.DELETION_PENDING
    )


def test_pre_request_standard_session_cannot_cancel(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    csrf_token = standard_login(client, seeded)
    stale_cookie = client.cookies.get("fantasy_session")
    assert request_deletion(client, csrf_token, slug=seeded.slug).status_code == 202

    client.cookies.set("fantasy_session", stale_cookie or "")
    client.cookies.set("fantasy_session_csrf", csrf_token)
    response = cancel(client, csrf_token)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.DELETION_PENDING
    )


def test_standard_session_cannot_cancel(client: TestClient, seeded: SeededWorkspace) -> None:
    csrf_token = standard_login(client, seeded)

    response = cancel(client, csrf_token)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert load_workspace(seeded.workspace_id).registration_status is (
        WorkspaceRegistrationStatus.ACTIVE
    )


# Password reset -------------------------------------------------------------------


def test_password_reset_works_without_reactivation(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)
    recovery_login(client, seeded)
    before = load_workspace(seeded.workspace_id)

    reset_request = client.post(
        "/api/auth/password-reset/request",
        headers=_pre_auth_headers(client),
        json={"email": seeded.administrator_email},
    )

    assert reset_request.status_code == status.HTTP_202_ACCEPTED

    with SessionFactory() as session:
        reset_deliveries = identity_deliveries(session, seeded.administrator_id, PASSWORD_RESET)

        assert len(reset_deliveries) == 1

        reset_delivery_id = reset_deliveries[0].id

    reset_token = deliver_and_extract_token(reset_delivery_id)

    new_password = "brand new administrator password"
    completed = client.post(
        "/api/auth/password-reset/complete",
        headers=_pre_auth_headers(client),
        json={"token": reset_token, "new_password": new_password},
    )

    assert completed.status_code == status.HTTP_200_OK
    after = load_workspace(seeded.workspace_id)
    assert after.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    assert after.deletion_requested_at == before.deletion_requested_at
    assert after.deletion_scheduled_for == before.deletion_scheduled_for
    assert all(item.revoked_at is not None for item in workspace_sessions(seeded.workspace_id))
    assert client.get("/api/auth/me").status_code == status.HTTP_401_UNAUTHORIZED

    assert admin_login(client, seeded.administrator_email).status_code == 401
    relogin = admin_login(client, seeded.administrator_email, new_password)
    assert relogin.json()["session_purpose"] == "deletion_recovery"


def test_password_reset_service_never_cancels_deletion(
    client: TestClient, seeded: SeededWorkspace
) -> None:
    put_in_deletion_pending(client, seeded)

    with SessionFactory() as session:
        administrator = session.get(User, seeded.administrator_id)
        assert administrator is not None
        issued = issue_password_reset_token(session, user=administrator, lifetime_minutes=30)
        session.commit()

    with SessionFactory() as session:
        reset_administrator_password(
            session,
            raw_token=issued.raw_token,
            new_password="another strong administrator password",
        )
        session.commit()

    workspace = load_workspace(seeded.workspace_id)
    assert workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING

    with SessionFactory() as session:
        assert (
            session.scalar(
                select(PasswordResetToken.id).where(
                    PasswordResetToken.user_id == seeded.administrator_id,
                    PasswordResetToken.used_at.is_(None),
                )
            )
            is None
        )
