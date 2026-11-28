"""API contract for replaced sessions (session_replaced).

The REPLACED state is produced by SessionRepository.replace_user_sessions
(used by deletion-recovery login); ordinary email login never replaces.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.auth_dependencies import get_current_user
from app.core.settings import get_settings
from app.db.auth_models import (
    AuthSession,
    LoginAttempt,
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
from app.db.session import SessionFactory, engine
from app.main import app
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository
from tests.support.identity_email import issue_password_reset_token

TRUSTED_ORIGIN = "http://localhost:5173"
ADMIN_PASSWORD = "correct horse battery staple"
SPECTATOR_PASSWORD = "shared spectator passphrase"
SESSION_REPLACED = {
    "detail": (
        "This administrator account was signed in from another device. Sign in again to continue."
    ),
    "code": "session_replaced",
    "retryable": False,
}
GENERIC_UNAUTHENTICATED = {
    "detail": "Authentication is required.",
}
REPLACING_USER_AGENT = "Replacing-Browser/9.9 (Distinctive Device Marker)"


@dataclass(frozen=True, slots=True)
class SeededWorkspace:
    workspace_id: int
    slug: str
    administrator_id: int
    administrator_email: str
    spectator_id: int


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


@pytest.fixture(autouse=True)
def real_authentication() -> Iterator[None]:
    inherited_override = app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    try:
        yield
    finally:
        if inherited_override is not None:
            app.dependency_overrides[get_current_user] = inherited_override


@pytest.fixture
def seeded_workspace() -> Iterator[SeededWorkspace]:
    suffix = uuid4().hex[:12]
    email = f"replacement-admin-{suffix}@example.com"
    slug = f"replacement-{suffix}"

    with SessionFactory() as session:
        workspace = Installation(
            application_name="Session Replacement League",
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
            display_name="Replacement Administrator",
            email=email,
            password=ADMIN_PASSWORD,
            role=UserRole.ADMIN,
            workspace_id=workspace.id,
        )
        administrator.email_verified_at = datetime.now(UTC)
        spectator = repository.create_user(
            username=f"spectator-{suffix}",
            display_name="Replacement Spectator",
            password=SPECTATOR_PASSWORD,
            role=UserRole.SPECTATOR,
            workspace_id=workspace.id,
        )
        session.commit()

        seeded = SeededWorkspace(
            workspace_id=workspace.id,
            slug=slug,
            administrator_id=administrator.id,
            administrator_email=email,
            spectator_id=spectator.id,
        )

    yield seeded

    with SessionFactory() as session:
        for user in session.scalars(select(User).where(User.workspace_id == seeded.workspace_id)):
            session.delete(user)

        session.flush()

        workspace = session.get(Installation, seeded.workspace_id)

        if workspace is not None:
            session.delete(workspace)

        session.commit()


def issue_csrf(client: TestClient) -> str:
    response = client.get("/api/auth/csrf")

    assert response.status_code == status.HTTP_200_OK

    return response.json()["csrf_token"]


def administrator_login(
    client: TestClient,
    seeded: SeededWorkspace,
    *,
    user_agent: str | None = None,
):
    headers = {
        "Origin": TRUSTED_ORIGIN,
        "X-CSRF-Token": issue_csrf(client),
    }

    if user_agent is not None:
        headers["User-Agent"] = user_agent

    response = client.post(
        "/api/auth/admin/login",
        headers=headers,
        json={
            "email": seeded.administrator_email,
            "password": ADMIN_PASSWORD,
        },
    )

    assert response.status_code == status.HTTP_200_OK

    return response


def replace_administrator_sessions(
    seeded: SeededWorkspace,
    *,
    user_agent: str | None = None,
) -> str:
    """Displace every active session with reason REPLACED (as deletion recovery does)."""

    with SessionFactory() as session:
        administrator = session.get(User, seeded.administrator_id)
        assert administrator is not None
        created = SessionRepository(session).replace_user_sessions(
            user=administrator,
            lifetime_hours=1,
            user_agent=user_agent,
        )
        session.commit()

        return created.session_token


def spectator_login(client: TestClient, seeded: SeededWorkspace):
    response = client.post(
        "/api/auth/spectator/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": issue_csrf(client),
        },
        json={
            "workspace_slug": seeded.slug,
            "password": SPECTATOR_PASSWORD,
        },
    )

    assert response.status_code == status.HTTP_200_OK

    return response


def session_cookie_name() -> str:
    return get_settings().session_cookie_name


def set_session_cookie(client: TestClient, value: str) -> None:
    client.cookies.clear()
    client.cookies.set(session_cookie_name(), value)


@pytest.mark.parametrize(
    "path",
    ["/api/auth/me", "/api/leagues/current", "/api/admin/sync-status"],
)
def test_replaced_browser_receives_session_replaced(
    seeded_workspace: SeededWorkspace,
    path: str,
) -> None:
    with TestClient(app) as browser_a, TestClient(app) as browser_b:
        administrator_login(browser_a, seeded_workspace)
        assert browser_a.get("/api/auth/me").status_code == status.HTTP_200_OK

        set_session_cookie(
            browser_b,
            replace_administrator_sessions(seeded_workspace, user_agent=REPLACING_USER_AGENT),
        )

        response = browser_a.get(path)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == SESSION_REPLACED
        assert browser_b.get("/api/auth/me").status_code == status.HTTP_200_OK


def test_session_replaced_response_discloses_no_device_details(
    seeded_workspace: SeededWorkspace,
) -> None:
    with TestClient(app) as browser_a, TestClient(app) as browser_b:
        administrator_login(browser_a, seeded_workspace)
        set_session_cookie(
            browser_b,
            replace_administrator_sessions(seeded_workspace, user_agent=REPLACING_USER_AGENT),
        )

        response = browser_a.get("/api/auth/me")

    assert response.json() == SESSION_REPLACED

    body = response.text
    headers = " ".join(f"{key}:{value}" for key, value in response.headers.items())

    for marker in (REPLACING_USER_AGENT, "Distinctive Device Marker", "testclient", "127.0.0.1"):
        assert marker not in body
        assert marker not in headers

    # Body-only: standard security headers legitimately mention
    # "geolocation" in Permissions-Policy.
    for term in ("user_agent", "ip_address", "browser", "location", "device_id"):
        assert term not in body


def test_session_replaced_survives_new_connections(
    seeded_workspace: SeededWorkspace,
) -> None:
    with TestClient(app) as browser_a:
        administrator_login(browser_a, seeded_workspace)
        stale_token = browser_a.cookies.get(session_cookie_name())
        replace_administrator_sessions(seeded_workspace)

    # Drop every pooled connection: the reason is read from the database,
    # not from any in-process state, so it survives a process restart.
    engine.dispose()

    with TestClient(app) as restarted_browser:
        assert stale_token is not None
        set_session_cookie(restarted_browser, stale_token)

        response = restarted_browser.get("/api/auth/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == SESSION_REPLACED


def test_logged_out_session_is_generic_not_replaced(
    seeded_workspace: SeededWorkspace,
) -> None:
    with TestClient(app) as browser:
        login = administrator_login(browser, seeded_workspace)
        token = browser.cookies.get(session_cookie_name())

        logout = browser.post(
            "/api/auth/logout",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": login.json()["csrf_token"],
            },
        )
        assert logout.status_code == status.HTTP_200_OK

        assert token is not None
        set_session_cookie(browser, token)
        response = browser.get("/api/auth/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == GENERIC_UNAUTHENTICATED

    with SessionFactory() as session:
        stored = session.scalars(
            select(AuthSession).where(AuthSession.user_id == seeded_workspace.administrator_id)
        ).all()

    assert [row.revocation_reason for row in stored] == [SessionRevocationReason.LOGOUT]


def test_password_reset_session_is_generic_not_replaced(
    seeded_workspace: SeededWorkspace,
) -> None:
    with TestClient(app) as browser:
        administrator_login(browser, seeded_workspace)
        token = browser.cookies.get(session_cookie_name())

    with SessionFactory() as session:
        administrator = session.get(User, seeded_workspace.administrator_id)
        assert administrator is not None
        issued = issue_password_reset_token(session, user=administrator, lifetime_minutes=30)
        session.commit()

    with TestClient(app) as reset_browser:
        completed = reset_browser.post(
            "/api/auth/password-reset/complete",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": issue_csrf(reset_browser),
            },
            json={
                "token": issued.raw_token,
                "new_password": "a completely new passphrase",
            },
        )
        assert completed.status_code == status.HTTP_200_OK

    with TestClient(app) as browser:
        assert token is not None
        set_session_cookie(browser, token)
        response = browser.get("/api/auth/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == GENERIC_UNAUTHENTICATED


def test_suspension_of_active_session_is_not_reported_as_replaced(
    seeded_workspace: SeededWorkspace,
) -> None:
    with TestClient(app) as browser:
        administrator_login(browser, seeded_workspace)

        with SessionFactory() as session:
            workspace = session.get(Installation, seeded_workspace.workspace_id)
            assert workspace is not None
            workspace.registration_status = WorkspaceRegistrationStatus.SUSPENDED
            session.commit()

        response = browser.get("/api/leagues/current")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "workspace_suspended"


def test_replaced_session_in_suspended_workspace_is_reported_as_replaced(
    seeded_workspace: SeededWorkspace,
) -> None:
    with TestClient(app) as browser_a, TestClient(app) as browser_b:
        administrator_login(browser_a, seeded_workspace)
        set_session_cookie(browser_b, replace_administrator_sessions(seeded_workspace))

        with SessionFactory() as session:
            workspace = session.get(Installation, seeded_workspace.workspace_id)
            assert workspace is not None
            workspace.registration_status = WorkspaceRegistrationStatus.SUSPENDED
            session.commit()

        response = browser_a.get("/api/leagues/current")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == SESSION_REPLACED


def test_second_email_login_does_not_replace_session(
    seeded_workspace: SeededWorkspace,
) -> None:
    with TestClient(app) as browser_a, TestClient(app) as browser_b:
        administrator_login(browser_a, seeded_workspace)
        administrator_login(browser_b, seeded_workspace)

        assert browser_a.get("/api/auth/me").status_code == status.HTTP_200_OK
        assert browser_b.get("/api/auth/me").status_code == status.HTTP_200_OK

    with SessionFactory() as session:
        stored = session.scalars(
            select(AuthSession).where(AuthSession.user_id == seeded_workspace.administrator_id)
        ).all()

    assert len(stored) == 2
    assert all(row.revoked_at is None for row in stored)
    assert all(row.revocation_reason is None for row in stored)


def test_spectator_sessions_remain_concurrent_and_unaffected(
    seeded_workspace: SeededWorkspace,
) -> None:
    with (
        TestClient(app) as spectator_one,
        TestClient(app) as spectator_two,
        TestClient(app) as admin_a,
        TestClient(app) as admin_b,
    ):
        spectator_login(spectator_one, seeded_workspace)
        spectator_login(spectator_two, seeded_workspace)
        administrator_login(admin_a, seeded_workspace)
        administrator_login(admin_b, seeded_workspace)

        assert spectator_one.get("/api/auth/me").status_code == status.HTTP_200_OK
        assert spectator_two.get("/api/auth/me").status_code == status.HTTP_200_OK

    with SessionFactory() as session:
        spectator_sessions = session.scalars(
            select(AuthSession).where(AuthSession.user_id == seeded_workspace.spectator_id)
        ).all()

    assert len(spectator_sessions) == 2
    assert all(row.revoked_at is None for row in spectator_sessions)
    assert all(row.revocation_reason is None for row in spectator_sessions)


@pytest.mark.parametrize(
    "cookie_value",
    [None, "", "malformed-session-token", "x" * 300],
)
def test_missing_and_malformed_sessions_remain_generic(
    cookie_value: str | None,
) -> None:
    with TestClient(app) as browser:
        if cookie_value is not None:
            set_session_cookie(browser, cookie_value)

        response = browser.get("/api/auth/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == GENERIC_UNAUTHENTICATED


def test_expired_session_remains_generic(
    seeded_workspace: SeededWorkspace,
) -> None:
    with SessionFactory() as session:
        administrator = session.get(User, seeded_workspace.administrator_id)
        assert administrator is not None
        expired = SessionRepository(session).create_session(
            user=administrator,
            lifetime_hours=1,
            now=datetime.now(UTC) - timedelta(hours=5),
        )
        session.commit()

    with TestClient(app) as browser:
        set_session_cookie(browser, expired.session_token)
        response = browser.get("/api/auth/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == GENERIC_UNAUTHENTICATED


@pytest.mark.parametrize(
    "reason",
    [
        SessionRevocationReason.SPECTATOR_PASSWORD_RESET,
        SessionRevocationReason.SPECTATOR_DISABLED,
    ],
)
def test_spectator_revocations_remain_generic(
    seeded_workspace: SeededWorkspace,
    reason: SessionRevocationReason,
) -> None:
    with TestClient(app) as browser:
        spectator_login(browser, seeded_workspace)
        token = browser.cookies.get(session_cookie_name())

    with SessionFactory() as session:
        SessionRepository(session).revoke_all_user_sessions(
            user_id=seeded_workspace.spectator_id,
            reason=reason,
        )
        session.commit()

    with TestClient(app) as browser:
        assert token is not None
        set_session_cookie(browser, token)
        response = browser.get("/api/auth/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == GENERIC_UNAUTHENTICATED
