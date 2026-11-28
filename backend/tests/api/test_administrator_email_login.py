from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.auth_dependencies import get_current_user
from app.db.auth_models import (
    AuthSession,
    LoginAttempt,
    User,
    UserRole,
)
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import (
    AuthenticationRepository,
)

TRUSTED_ORIGIN = "http://localhost:5173"


@pytest.fixture(autouse=True)
def isolate_administrator_login_throttle_rows() -> Iterator[None]:
    """Remove throttle rows created by each email-login test."""

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
def active_administrator() -> Iterator[tuple[str, str, int]]:
    suffix = uuid4().hex[:12]
    email = f"admin-email-{suffix}@example.com"
    password = "correct horse battery staple"

    with SessionFactory() as session:
        workspace = Installation(
            application_name="Administrator Email Login",
            slug=f"admin-email-{suffix}",
            normalized_slug=f"admin-email-{suffix}",
            timezone="America/Chicago",
            status=InstallationStatus.ACTIVE,
            registration_status=(WorkspaceRegistrationStatus.ACTIVE),
            onboarding_stage=OnboardingStage.COMPLETE,
        )
        session.add(workspace)
        session.flush()

        administrator = AuthenticationRepository(session).create_user(
            username=f"internal-admin-{suffix}",
            display_name="Email Administrator",
            email=email,
            password=password,
            role=UserRole.ADMIN,
            workspace_id=workspace.id,
        )
        administrator.email_verified_at = datetime.now(UTC)

        session.commit()
        administrator_id = administrator.id

    yield email, password, administrator_id

    with SessionFactory() as session:
        administrator = session.get(
            User,
            administrator_id,
        )

        if administrator is not None:
            workspace = session.get(
                Installation,
                administrator.workspace_id,
            )
            session.delete(administrator)
            session.flush()

            if workspace is not None:
                session.delete(workspace)

            session.commit()


def issue_csrf(
    client: TestClient,
) -> str:
    response = client.get("/api/auth/csrf")

    assert response.status_code == status.HTTP_200_OK

    return response.json()["csrf_token"]


def administrator_login(
    client: TestClient,
    *,
    email: str,
    password: str,
):
    csrf_token = issue_csrf(client)

    return client.post(
        "/api/auth/admin/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "email": email,
            "password": password,
        },
    )


def test_administrator_email_login_creates_session(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, password, administrator_id = active_administrator

    response = administrator_login(
        client,
        email=f"  {email.upper()}  ",
        password=password,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["user"] == {
        "workspace_id": response.json()["user"]["workspace_id"],
        "display_name": "Email Administrator",
        "email": email,
        "email_verified": True,
        "role": "admin",
        "is_active": True,
    }
    assert response.json()["csrf_token"]
    assert "username" not in response.json()["user"]
    assert "fantasy_session" in client.cookies
    assert "fantasy_session_csrf" in client.cookies


def test_invalid_email_login_is_generic(
    client: TestClient,
) -> None:
    response = administrator_login(
        client,
        email=f"missing-{uuid4().hex}@example.com",
        password="incorrect password",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {
        "detail": "Invalid email or password.",
    }


def test_failed_login_preserves_existing_admin_session(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, password, administrator_id = active_administrator

    first = administrator_login(
        client,
        email=email,
        password=password,
    )

    assert first.status_code == status.HTTP_200_OK

    existing_cookie = client.cookies.get("fantasy_session")

    failed = administrator_login(
        client,
        email=email,
        password="incorrect password",
    )

    assert failed.status_code == status.HTTP_401_UNAUTHORIZED

    with SessionFactory() as session:
        active_count = len(
            session.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == administrator_id,
                    AuthSession.revoked_at.is_(None),
                )
            ).all()
        )

    assert active_count == 1
    assert client.cookies.get("fantasy_session") == existing_cookie


def test_second_admin_login_keeps_both_sessions_active(
    active_administrator: tuple[str, str, int],
) -> None:
    email, password, administrator_id = active_administrator

    with TestClient(app) as first_browser:
        first = administrator_login(
            first_browser,
            email=email,
            password=password,
        )

        assert first.status_code == status.HTTP_200_OK

        with TestClient(app) as second_browser:
            second = administrator_login(
                second_browser,
                email=email,
                password=password,
            )

            assert second.status_code == status.HTTP_200_OK

        with SessionFactory() as session:
            sessions = session.scalars(
                select(AuthSession)
                .where(
                    AuthSession.user_id == administrator_id,
                )
                .order_by(AuthSession.id)
            ).all()

        assert len(sessions) == 2
        assert all(item.revoked_at is None for item in sessions)
        assert all(item.revocation_reason is None for item in sessions)


def test_unverified_admin_login_is_generic(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, password, administrator_id = active_administrator

    with SessionFactory() as session:
        administrator = session.get(
            User,
            administrator_id,
        )

        assert administrator is not None

        administrator.email_verified_at = None
        session.commit()

    response = administrator_login(
        client,
        email=email,
        password=password,
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {
        "detail": "Invalid email or password.",
    }


def test_suspended_workspace_login_is_generic(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, password, administrator_id = active_administrator

    with SessionFactory() as session:
        administrator = session.get(
            User,
            administrator_id,
        )

        assert administrator is not None
        assert administrator.workspace_id is not None

        workspace = session.get(
            Installation,
            administrator.workspace_id,
        )

        assert workspace is not None

        workspace.registration_status = WorkspaceRegistrationStatus.SUSPENDED
        session.commit()

    response = administrator_login(
        client,
        email=email,
        password=password,
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {
        "detail": "Invalid email or password.",
    }


def test_repeated_administrator_failures_are_throttled_account_wide(
    active_administrator: tuple[str, str, int],
) -> None:
    """Email login inherits the account-wide administrator lockout."""

    email, password, administrator_id = active_administrator
    inherited_override = app.dependency_overrides.pop(get_current_user, None)
    throttled = None

    try:
        with TestClient(app) as first_browser:
            for _ in range(12):
                response = administrator_login(
                    first_browser,
                    email=email,
                    password="incorrect password",
                )

                if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                    throttled = response
                    break

                assert response.status_code == status.HTTP_401_UNAUTHORIZED

        assert throttled is not None
        assert throttled.json() == {"detail": "Invalid email or password."}
        assert int(throttled.headers["Retry-After"]) > 0

        # A different browser with the correct password is still refused.
        with TestClient(app) as second_browser:
            blocked = administrator_login(second_browser, email=email, password=password)
    finally:
        if inherited_override is not None:
            app.dependency_overrides[get_current_user] = inherited_override

    assert blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    with SessionFactory() as session:
        sessions = session.scalars(
            select(AuthSession).where(AuthSession.user_id == administrator_id)
        ).all()

    assert sessions == []
