"""GET /api/auth/me is authoritative for workspace lifecycle and access."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import _build_workspace_lifecycle
from app.api.auth_dependencies import get_current_user
from app.core.settings import get_settings
from app.db.auth_models import User, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository
from app.services.workspace_lifecycle import (
    WorkspaceAccess,
    resolve_workspace_access,
)

USER_KEYS = {
    "authenticated",
    "display_name",
    "email",
    "email_verified",
    "navigation_state",
    "legacy_workspace",
    "session_purpose",
}
WORKSPACE_KEYS = {
    "workspace_id",
    "workspace_slug",
    "installation_status",
    "registration_status",
    "onboarding_stage",
    "workspace_access",
}


@dataclass(frozen=True, slots=True)
class SeededMembers:
    workspace_id: int
    slug: str
    administrator_id: int
    administrator_username: str
    administrator_session: str
    spectator_id: int
    spectator_username: str
    spectator_session: str


@pytest.fixture(autouse=True)
def real_authentication() -> Iterator[None]:
    inherited_override = app.dependency_overrides.pop(get_current_user, None)

    try:
        yield
    finally:
        if inherited_override is not None:
            app.dependency_overrides[get_current_user] = inherited_override


@pytest.fixture
def seed_members() -> Iterator[Callable[..., SeededMembers]]:
    """Seed a workspace in any lifecycle with an administrator and spectator.

    Sessions are issued directly so a lifecycle that login would reject
    (for example a spectator in a provisional workspace) can still be
    observed through /me.
    """

    created: list[int] = []

    def create(
        *,
        installation_status: InstallationStatus,
        registration_status: WorkspaceRegistrationStatus | None,
        onboarding_stage: OnboardingStage | None,
    ) -> SeededMembers:
        suffix = uuid4().hex[:12]
        slug = f"me-lifecycle-{suffix}"

        with SessionFactory() as session:
            is_pending = registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
            workspace = Installation(
                application_name="Lifecycle League",
                slug=slug,
                normalized_slug=slug,
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                deletion_requested_at=datetime.now(UTC) if is_pending else None,
                deletion_scheduled_for=(
                    datetime.now(UTC) + timedelta(days=7) if is_pending else None
                ),
                onboarding_stage=onboarding_stage,
            )
            session.add(workspace)
            session.flush()
            created.append(workspace.id)

            repository = AuthenticationRepository(session)
            administrator = repository.create_user(
                username=f"internal-admin-{suffix}",
                display_name="Lifecycle Administrator",
                email=f"lifecycle-{suffix}@example.com",
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            administrator.email_verified_at = datetime.now(UTC)
            spectator = repository.create_user(
                username=f"internal-spectator-{suffix}",
                display_name="Lifecycle Spectator",
                password="shared spectator passphrase",
                role=UserRole.SPECTATOR,
                workspace_id=workspace.id,
            )
            session.flush()

            sessions = SessionRepository(session)
            administrator_session = sessions.create_session(user=administrator, lifetime_hours=1)
            spectator_session = sessions.create_session(user=spectator, lifetime_hours=1)
            session.commit()

            return SeededMembers(
                workspace_id=workspace.id,
                slug=slug,
                administrator_id=administrator.id,
                administrator_username=administrator.username,
                administrator_session=administrator_session.session_token,
                spectator_id=spectator.id,
                spectator_username=spectator.username,
                spectator_session=spectator_session.session_token,
            )

    yield create

    with SessionFactory() as session:
        for workspace_id in created:
            for user in session.scalars(select(User).where(User.workspace_id == workspace_id)):
                session.delete(user)

            session.flush()
            workspace = session.get(Installation, workspace_id)

            if workspace is not None:
                session.delete(workspace)

        session.commit()


def read_me(session_token: str) -> dict:
    with TestClient(app) as client:
        client.cookies.set(get_settings().session_cookie_name, session_token)
        response = client.get("/api/auth/me")

    assert response.status_code == status.HTTP_200_OK

    body = response.json()
    assert set(body) == USER_KEYS
    assert set(body["legacy_workspace"]) == {"role", "workspace"}
    assert set(body["legacy_workspace"]["workspace"]) == WORKSPACE_KEYS
    assert body["authenticated"] is True
    # Sessions issued outside the FR-20 recovery login are standard.
    assert body["session_purpose"] == "standard"

    return body


INACTIVE_DETAIL = {
    "installation_status": None,
    "registration_status": None,
    "onboarding_stage": None,
    "workspace_access": "inactive",
}


def test_active_legacy_administrator(seed_members: Callable[..., SeededMembers]) -> None:
    seeded = seed_members(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=None,
        onboarding_stage=None,
    )

    body = read_me(seeded.administrator_session)

    assert "id" not in body and "role" not in body and "is_active" not in body
    assert body["navigation_state"] == "legacy_workspace"
    assert body["legacy_workspace"]["role"] == "admin"
    assert body["legacy_workspace"]["workspace"] == {
        "workspace_id": seeded.workspace_id,
        "workspace_slug": seeded.slug,
        "installation_status": "active",
        "registration_status": None,
        "onboarding_stage": None,
        "workspace_access": "active",
    }


def test_active_saas_administrator(seed_members: Callable[..., SeededMembers]) -> None:
    seeded = seed_members(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=WorkspaceRegistrationStatus.ACTIVE,
        onboarding_stage=OnboardingStage.COMPLETE,
    )

    workspace = read_me(seeded.administrator_session)["legacy_workspace"]["workspace"]

    assert workspace["installation_status"] == "active"
    assert workspace["registration_status"] == "active"
    assert workspace["onboarding_stage"] == "complete"
    assert workspace["workspace_access"] == "active"


@pytest.mark.parametrize(
    "stage",
    [
        OnboardingStage.PROVIDER_SETUP_PENDING,
        OnboardingStage.LEAGUE_CLAIM_PENDING,
        OnboardingStage.INITIAL_SYNC_FAILED,
    ],
)
def test_verified_provisional_administrator_returns_exact_stage(
    seed_members: Callable[..., SeededMembers],
    stage: OnboardingStage,
) -> None:
    seeded = seed_members(
        installation_status=InstallationStatus.SETUP_IN_PROGRESS,
        registration_status=WorkspaceRegistrationStatus.PROVISIONAL,
        onboarding_stage=stage,
    )

    workspace = read_me(seeded.administrator_session)["legacy_workspace"]["workspace"]

    assert workspace == {
        "workspace_id": seeded.workspace_id,
        "workspace_slug": seeded.slug,
        "installation_status": "setup_in_progress",
        "registration_status": "provisional",
        "onboarding_stage": stage.value,
        "workspace_access": "provisional",
    }


def test_unverified_provisional_stage_is_never_resumable(
    seed_members: Callable[..., SeededMembers],
) -> None:
    seeded = seed_members(
        installation_status=InstallationStatus.SETUP_REQUIRED,
        registration_status=WorkspaceRegistrationStatus.PROVISIONAL,
        onboarding_stage=OnboardingStage.EMAIL_VERIFICATION_PENDING,
    )

    workspace = read_me(seeded.administrator_session)["legacy_workspace"]["workspace"]

    assert workspace == {"workspace_id": seeded.workspace_id, "workspace_slug": seeded.slug} | (
        INACTIVE_DETAIL
    )


@pytest.mark.parametrize("member", ["administrator", "spectator"])
def test_suspended_workspace(
    seed_members: Callable[..., SeededMembers],
    member: str,
) -> None:
    seeded = seed_members(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=WorkspaceRegistrationStatus.SUSPENDED,
        onboarding_stage=OnboardingStage.COMPLETE,
    )

    workspace = read_me(getattr(seeded, f"{member}_session"))["legacy_workspace"]["workspace"]

    assert workspace["registration_status"] == "suspended"
    assert workspace["workspace_access"] == "suspended"


@pytest.mark.parametrize(
    ("installation_status", "registration_status", "stage"),
    [
        (
            InstallationStatus.ACTIVE,
            WorkspaceRegistrationStatus.DELETION_PENDING,
            OnboardingStage.COMPLETE,
        ),
        (
            InstallationStatus.ACTIVE,
            WorkspaceRegistrationStatus.DELETED,
            OnboardingStage.COMPLETE,
        ),
        # Inconsistent: SaaS-active registration on an unfinished install.
        (
            InstallationStatus.SETUP_IN_PROGRESS,
            WorkspaceRegistrationStatus.ACTIVE,
            OnboardingStage.COMPLETE,
        ),
        # Inconsistent: provisional registration on an active install.
        (
            InstallationStatus.ACTIVE,
            WorkspaceRegistrationStatus.PROVISIONAL,
            OnboardingStage.PROVIDER_SETUP_PENDING,
        ),
    ],
)
def test_unusable_workspace_is_inactive_without_detail(
    seed_members: Callable[..., SeededMembers],
    installation_status: InstallationStatus,
    registration_status: WorkspaceRegistrationStatus,
    stage: OnboardingStage,
) -> None:
    seeded = seed_members(
        installation_status=installation_status,
        registration_status=registration_status,
        onboarding_stage=stage,
    )

    for token in (seeded.administrator_session, seeded.spectator_session):
        workspace = read_me(token)["legacy_workspace"]["workspace"]

        assert (
            workspace
            == {
                "workspace_id": seeded.workspace_id,
                "workspace_slug": seeded.slug,
            }
            | INACTIVE_DETAIL
        )


def test_active_spectator(seed_members: Callable[..., SeededMembers]) -> None:
    seeded = seed_members(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=WorkspaceRegistrationStatus.ACTIVE,
        onboarding_stage=OnboardingStage.COMPLETE,
    )

    body = read_me(seeded.spectator_session)

    assert "id" not in body and "role" not in body
    assert body["legacy_workspace"]["role"] == "spectator"
    assert body["legacy_workspace"]["workspace"] == {
        "workspace_id": seeded.workspace_id,
        "workspace_slug": seeded.slug,
        "installation_status": "active",
        "registration_status": "active",
        # Onboarding progress is administrator information.
        "onboarding_stage": None,
        "workspace_access": "active",
    }


def test_spectator_in_provisional_workspace_is_inactive_not_provisional(
    seed_members: Callable[..., SeededMembers],
) -> None:
    seeded = seed_members(
        installation_status=InstallationStatus.SETUP_IN_PROGRESS,
        registration_status=WorkspaceRegistrationStatus.PROVISIONAL,
        onboarding_stage=OnboardingStage.LEAGUE_CLAIM_PENDING,
    )

    workspace = read_me(seeded.spectator_session)["legacy_workspace"]["workspace"]

    assert (
        workspace
        == {
            "workspace_id": seeded.workspace_id,
            "workspace_slug": seeded.slug,
        }
        | INACTIVE_DETAIL
    )


def test_response_excludes_usernames_and_secrets(
    seed_members: Callable[..., SeededMembers],
) -> None:
    seeded = seed_members(
        installation_status=InstallationStatus.SETUP_IN_PROGRESS,
        registration_status=WorkspaceRegistrationStatus.PROVISIONAL,
        onboarding_stage=OnboardingStage.PROVIDER_SETUP_PENDING,
    )

    for token, username in (
        (seeded.administrator_session, seeded.administrator_username),
        (seeded.spectator_session, seeded.spectator_username),
    ):
        with TestClient(app) as client:
            client.cookies.set(get_settings().session_cookie_name, token)
            raw = client.get("/api/auth/me").text

        assert username not in raw
        assert token not in raw

        for marker in (
            "username",
            "password",
            "hash",
            "token",
            "revoc",
            "credential",
            "provider_league",
            "external",
            "owner",
            "public_url",
            "application_name",
            "Lifecycle League",
        ):
            assert marker not in raw


def test_each_user_receives_only_their_own_workspace(
    seed_members: Callable[..., SeededMembers],
) -> None:
    active = seed_members(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=WorkspaceRegistrationStatus.ACTIVE,
        onboarding_stage=OnboardingStage.COMPLETE,
    )
    suspended = seed_members(
        installation_status=InstallationStatus.ACTIVE,
        registration_status=WorkspaceRegistrationStatus.SUSPENDED,
        onboarding_stage=OnboardingStage.COMPLETE,
    )

    with TestClient(app) as client:
        client.cookies.set(get_settings().session_cookie_name, active.administrator_session)
        active_raw = client.get("/api/auth/me").text

    with TestClient(app) as client:
        client.cookies.set(get_settings().session_cookie_name, suspended.spectator_session)
        suspended_raw = client.get("/api/auth/me").text

    assert active.slug in active_raw and suspended.slug not in active_raw
    assert suspended.slug in suspended_raw and active.slug not in suspended_raw
    active_me = read_me(active.administrator_session)
    suspended_me = read_me(suspended.spectator_session)
    assert active_me["legacy_workspace"]["workspace"]["workspace_access"] == "active"
    assert suspended_me["legacy_workspace"]["workspace"]["workspace_access"] == "suspended"


def test_missing_workspace_is_inactive() -> None:
    for role in UserRole:
        assert resolve_workspace_access(None, role) is WorkspaceAccess.INACTIVE

        lifecycle = _build_workspace_lifecycle(workspace_id=404, workspace=None, role=role)

        assert (
            lifecycle.model_dump(mode="json")
            == {
                "workspace_id": 404,
                "workspace_slug": None,
            }
            | INACTIVE_DETAIL
        )
