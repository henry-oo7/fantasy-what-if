"""Legacy setup must never drive or be broken by SaaS-managed workspaces."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_current_user
from app.db.auth_models import LoginAttempt, User, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation, ProviderConnection
from app.db.session import SessionFactory, engine
from app.main import app
from app.repositories.auth_repository import AuthenticationRepository
from app.services.setup_bootstrap import (
    SetupBootstrapUnavailableError,
    bootstrap_administrator,
)
from app.services.setup_status import resolve_setup_state

TRUSTED_ORIGIN = "http://localhost:5173"
ADMIN_PASSWORD = "correct horse battery staple"
LEGACY_SETUP_UNAVAILABLE = {
    "detail": "Legacy setup is unavailable for this workspace.",
}


def provisional_workspace(suffix: str) -> Installation:
    slug = f"legacy-retire-{suffix}"

    return Installation(
        application_name="Provisional SaaS League",
        slug=slug,
        normalized_slug=slug,
        timezone="America/Chicago",
        status=InstallationStatus.SETUP_IN_PROGRESS,
        registration_status=WorkspaceRegistrationStatus.PROVISIONAL,
        onboarding_stage=OnboardingStage.PROVIDER_SETUP_PENDING,
    )


@pytest.fixture(autouse=True)
def isolate_login_attempt_rows() -> Iterator[None]:
    with SessionFactory() as session:
        existing_ids = set(session.scalars(select(LoginAttempt.id)).all())

    try:
        yield
    finally:
        with SessionFactory() as session:
            cleanup = delete(LoginAttempt)

            if existing_ids:
                cleanup = cleanup.where(LoginAttempt.id.not_in(existing_ids))

            session.execute(cleanup)
            session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    inherited_override = app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        if inherited_override is not None:
            app.dependency_overrides[get_current_user] = inherited_override


@pytest.fixture
def provisional_administrator() -> Iterator[tuple[int, str]]:
    """Commit a verified administrator of a provisional SaaS workspace."""

    suffix = uuid4().hex[:12]
    email = f"legacy-retire-{suffix}@example.com"

    with SessionFactory() as session:
        workspace = provisional_workspace(suffix)
        session.add(workspace)
        session.flush()

        administrator = AuthenticationRepository(session).create_user(
            username=f"internal-admin-{suffix}",
            display_name="Provisional Administrator",
            email=email,
            password=ADMIN_PASSWORD,
            role=UserRole.ADMIN,
            workspace_id=workspace.id,
        )
        administrator.email_verified_at = datetime.now(UTC)
        session.commit()
        workspace_id = workspace.id

    yield workspace_id, email

    with SessionFactory() as session:
        for user in session.scalars(select(User).where(User.workspace_id == workspace_id)):
            session.delete(user)

        session.flush()

        workspace = session.get(Installation, workspace_id)

        if workspace is not None:
            session.delete(workspace)

        session.commit()


@pytest.fixture
def database_session() -> Iterator[Session]:
    """Rolled-back transaction for service-level lifecycle checks."""

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


def workspace_state(workspace_id: int) -> tuple[object, ...]:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)
        assert workspace is not None

        provider_count = session.scalar(
            select(func.count())
            .select_from(ProviderConnection)
            .where(ProviderConnection.installation_id == workspace_id)
        )

        return (
            workspace.status,
            workspace.registration_status,
            workspace.onboarding_stage,
            workspace.active_league_id,
            workspace.setup_completed_at,
            provider_count,
        )


def log_in(client: TestClient, *, email: str) -> str:
    csrf_response = client.get("/api/auth/csrf")
    assert csrf_response.status_code == status.HTTP_200_OK

    response = client.post(
        "/api/auth/admin/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_response.json()["csrf_token"],
        },
        json={
            "email": email,
            "password": ADMIN_PASSWORD,
        },
    )

    assert response.status_code == status.HTTP_200_OK

    return response.json()["csrf_token"]


def test_provisional_admin_cannot_use_legacy_provider_connection(
    client: TestClient,
    provisional_administrator: tuple[int, str],
) -> None:
    workspace_id, email = provisional_administrator
    csrf_token = log_in(client, email=email)
    before = workspace_state(workspace_id)

    response = client.post(
        "/api/setup/provider-connection",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "provider": "espn",
            "league_id": "123456",
            "season": "2025",
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == LEGACY_SETUP_UNAVAILABLE
    assert workspace_state(workspace_id) == before


def test_provisional_admin_cannot_use_legacy_finalize(
    client: TestClient,
    provisional_administrator: tuple[int, str],
) -> None:
    workspace_id, email = provisional_administrator
    csrf_token = log_in(client, email=email)
    before = workspace_state(workspace_id)

    response = client.post(
        "/api/setup/finalize",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == LEGACY_SETUP_UNAVAILABLE
    assert workspace_state(workspace_id) == before


def test_bootstrap_admin_is_rejected_and_leaves_saas_workspace_unchanged(
    client: TestClient,
    provisional_administrator: tuple[int, str],
) -> None:
    workspace_id, _ = provisional_administrator
    before = workspace_state(workspace_id)

    with SessionFactory() as session:
        user_count = session.scalar(select(func.count()).select_from(User))

    csrf_response = client.get("/api/auth/csrf")
    response = client.post(
        "/api/setup/bootstrap-admin",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_response.json()["csrf_token"],
        },
        json={
            "username": f"bootstrap-{uuid4().hex[:8]}",
            "display_name": "Bootstrap Attempt",
            "password": ADMIN_PASSWORD,
        },
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert workspace_state(workspace_id) == before

    with SessionFactory() as session:
        assert session.scalar(select(func.count()).select_from(User)) == user_count


def test_setup_status_ignores_provisional_saas_workspaces(
    client: TestClient,
    provisional_administrator: tuple[int, str],
) -> None:
    del provisional_administrator

    response = client.get("/api/setup/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] in (
        "setup_required",
        "setup_in_progress",
        "active",
    )


def test_setup_state_excludes_saas_rows_at_service_level(
    database_session: Session,
) -> None:
    baseline = resolve_setup_state(database_session)

    for _ in range(2):
        database_session.add(provisional_workspace(uuid4().hex[:12]))

    database_session.flush()

    assert resolve_setup_state(database_session) == baseline


def test_bootstrap_service_never_targets_saas_rows(
    database_session: Session,
) -> None:
    workspaces = [provisional_workspace(uuid4().hex[:12]) for _ in range(2)]
    database_session.add_all(workspaces)
    database_session.flush()

    with pytest.raises(SetupBootstrapUnavailableError):
        bootstrap_administrator(
            database_session,
            username=f"bootstrap-{uuid4().hex[:8]}",
            display_name="Bootstrap Attempt",
            email=None,
            password=ADMIN_PASSWORD,
        )

    for workspace in workspaces:
        database_session.refresh(workspace)
        assert workspace.status is InstallationStatus.SETUP_IN_PROGRESS
        assert workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
