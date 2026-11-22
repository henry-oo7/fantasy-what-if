from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.auth_dependencies import get_current_user
from app.api.registration_dependencies import (
    get_transactional_email_sender,
)
from app.auth.security import verify_password
from app.db.auth_models import (
    AuthSession,
    PasswordResetToken,
    RegistrationRateLimit,
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
from app.notifications.transactional_email import (
    TransactionalEmailDeliveryError,
)
from app.repositories.auth_repository import (
    AuthenticationRepository,
)
from app.repositories.session_repository import (
    SessionRepository,
)

TRUSTED_ORIGIN = "http://localhost:5173"


class CapturingPasswordResetEmailSender:
    def __init__(self) -> None:
        self.password_reset_messages: list[dict[str, str]] = []

    def send_email_verification(
        self,
        *,
        recipient_email: str,
        recipient_display_name: str,
        verification_token: str,
    ) -> None:
        del (
            recipient_email,
            recipient_display_name,
            verification_token,
        )

    def send_password_reset(
        self,
        *,
        recipient_email: str,
        recipient_display_name: str,
        password_reset_token: str,
    ) -> None:
        self.password_reset_messages.append(
            {
                "recipient_email": recipient_email,
                "recipient_display_name": (recipient_display_name),
                "password_reset_token": (password_reset_token),
            }
        )


class FailingPasswordResetEmailSender:
    def send_email_verification(
        self,
        *,
        recipient_email: str,
        recipient_display_name: str,
        verification_token: str,
    ) -> None:
        del (
            recipient_email,
            recipient_display_name,
            verification_token,
        )

    def send_password_reset(
        self,
        *,
        recipient_email: str,
        recipient_display_name: str,
        password_reset_token: str,
    ) -> None:
        del (
            recipient_email,
            recipient_display_name,
            password_reset_token,
        )

        raise TransactionalEmailDeliveryError("Synthetic password-reset delivery failure.")


@pytest.fixture(autouse=True)
def isolate_password_reset_rate_limit_rows() -> Iterator[None]:
    with SessionFactory() as session:
        existing_ids = set(session.scalars(select(RegistrationRateLimit.id)).all())

    try:
        yield
    finally:
        with SessionFactory() as session:
            cleanup = delete(RegistrationRateLimit)

            if existing_ids:
                cleanup = cleanup.where(RegistrationRateLimit.id.not_in(existing_ids))

            session.execute(cleanup)
            session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    inherited_user_override = app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(
            get_transactional_email_sender,
            None,
        )

        if inherited_user_override is not None:
            app.dependency_overrides[get_current_user] = inherited_user_override


@pytest.fixture
def active_administrator() -> Iterator[tuple[str, str, int]]:
    suffix = uuid4().hex[:12]
    email = f"password-reset-{suffix}@example.com"
    password = "correct horse battery staple"

    with SessionFactory() as session:
        workspace = Installation(
            application_name=("Administrator Password Recovery"),
            slug=f"password-reset-{suffix}",
            normalized_slug=f"password-reset-{suffix}",
            timezone="America/Chicago",
            status=InstallationStatus.ACTIVE,
            registration_status=(WorkspaceRegistrationStatus.ACTIVE),
            onboarding_stage=OnboardingStage.COMPLETE,
        )
        session.add(workspace)
        session.flush()

        administrator = AuthenticationRepository(session).create_user(
            username=f"reset-admin-{suffix}",
            display_name="Password Reset Administrator",
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


def request_password_reset(
    client: TestClient,
    *,
    email: str,
):
    csrf_token = issue_csrf(client)

    return client.post(
        "/api/auth/password-reset/request",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "email": email,
        },
    )


def complete_password_reset(
    client: TestClient,
    *,
    token: str,
    new_password: str,
):
    csrf_token = issue_csrf(client)

    return client.post(
        "/api/auth/password-reset/complete",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "token": token,
            "new_password": new_password,
        },
    )


def test_password_reset_request_requires_pre_auth_csrf(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/auth/password-reset/request",
        headers={
            "Origin": TRUSTED_ORIGIN,
        },
        json={
            "email": "administrator@example.com",
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "CSRF validation failed.",
    }


def test_password_reset_request_rejects_untrusted_origin(
    client: TestClient,
) -> None:
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/auth/password-reset/request",
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
        json={
            "email": "administrator@example.com",
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "Request origin is not allowed.",
    }


def test_eligible_administrator_receives_reset_token(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, _, administrator_id = active_administrator
    sender = CapturingPasswordResetEmailSender()

    app.dependency_overrides[get_transactional_email_sender] = lambda: sender

    response = request_password_reset(
        client,
        email=f"  {email.upper()}  ",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == {
        "detail": (
            "If an eligible administrator account exists "
            "for that email address, password reset "
            "instructions have been sent."
        ),
        "code": "password_reset_request_accepted",
    }

    assert len(sender.password_reset_messages) == 1

    message = sender.password_reset_messages[0]

    assert message["recipient_email"] == email
    assert message["recipient_display_name"] == ("Password Reset Administrator")
    assert message["password_reset_token"]
    assert message["password_reset_token"] not in response.text

    with SessionFactory() as session:
        stored = session.scalar(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == administrator_id,
                PasswordResetToken.used_at.is_(None),
            )
        )

    assert stored is not None
    assert stored.token_hash != message["password_reset_token"]


def test_unknown_email_receives_same_generic_response(
    client: TestClient,
) -> None:
    sender = CapturingPasswordResetEmailSender()

    app.dependency_overrides[get_transactional_email_sender] = lambda: sender

    response = request_password_reset(
        client,
        email=f"missing-{uuid4().hex}@example.com",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == {
        "detail": (
            "If an eligible administrator account exists "
            "for that email address, password reset "
            "instructions have been sent."
        ),
        "code": "password_reset_request_accepted",
    }
    assert sender.password_reset_messages == []


def test_delivery_failure_remains_enumeration_safe(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, _, _ = active_administrator

    app.dependency_overrides[get_transactional_email_sender] = lambda: (
        FailingPasswordResetEmailSender()
    )

    response = request_password_reset(
        client,
        email=email,
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json()["code"] == ("password_reset_request_accepted")
    assert "delivery" not in response.text.casefold()


def test_new_reset_request_invalidates_prior_token(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, _, _ = active_administrator
    sender = CapturingPasswordResetEmailSender()

    app.dependency_overrides[get_transactional_email_sender] = lambda: sender

    first = request_password_reset(
        client,
        email=email,
    )
    second = request_password_reset(
        client,
        email=email,
    )

    assert first.status_code == status.HTTP_202_ACCEPTED
    assert second.status_code == status.HTTP_202_ACCEPTED
    assert len(sender.password_reset_messages) == 2

    first_token = sender.password_reset_messages[0]["password_reset_token"]
    second_token = sender.password_reset_messages[1]["password_reset_token"]

    assert first_token != second_token

    first_completion = complete_password_reset(
        client,
        token=first_token,
        new_password="replacement horse battery staple",
    )

    assert first_completion.status_code == (status.HTTP_400_BAD_REQUEST)
    assert first_completion.json() == {
        "detail": ("The password reset link is invalid or expired."),
        "code": "invalid_password_reset",
        "retryable": False,
    }

    second_completion = complete_password_reset(
        client,
        token=second_token,
        new_password="replacement horse battery staple",
    )

    assert second_completion.status_code == status.HTTP_200_OK


def test_password_reset_request_is_limited_after_three_requests(
    client: TestClient,
) -> None:
    email = f"limited-{uuid4().hex}@example.com"

    responses = [
        request_password_reset(
            client,
            email=email,
        )
        for _ in range(4)
    ]

    for response in responses[:3]:
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json()["code"] == ("password_reset_request_accepted")

    blocked = responses[3]

    assert blocked.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)
    assert blocked.json() == {
        "detail": ("Password reset is temporarily unavailable. Try again later."),
        "code": "password_reset_throttled",
        "retryable": True,
    }

    retry_after = int(blocked.headers["Retry-After"])

    assert 1 <= retry_after <= 60 * 60


def test_invalid_reset_token_returns_stable_error(
    client: TestClient,
) -> None:
    response = complete_password_reset(
        client,
        token="invalid-password-reset-token",
        new_password="replacement horse battery staple",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": ("The password reset link is invalid or expired."),
        "code": "invalid_password_reset",
        "retryable": False,
    }


def test_password_schema_rejects_short_replacement(
    client: TestClient,
) -> None:
    response = complete_password_reset(
        client,
        token="synthetic-password-reset-token",
        new_password="too-short",
    )

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)


def test_completed_reset_revokes_sessions_and_requires_login(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, old_password, administrator_id = active_administrator
    sender = CapturingPasswordResetEmailSender()

    app.dependency_overrides[get_transactional_email_sender] = lambda: sender

    with SessionFactory() as session:
        administrator = session.get(
            User,
            administrator_id,
        )

        assert administrator is not None

        first_session = SessionRepository(session).create_session(
            user=administrator,
            lifetime_hours=24,
        )
        second_session = SessionRepository(session).create_session(
            user=administrator,
            lifetime_hours=24,
        )
        session.commit()

    request_response = request_password_reset(
        client,
        email=email,
    )

    assert request_response.status_code == (status.HTTP_202_ACCEPTED)
    assert len(sender.password_reset_messages) == 1

    raw_token = sender.password_reset_messages[0]["password_reset_token"]
    new_password = "replacement horse battery staple"

    completion = complete_password_reset(
        client,
        token=raw_token,
        new_password=new_password,
    )

    assert completion.status_code == status.HTTP_200_OK
    assert completion.json() == {
        "detail": ("Your password has been reset. Sign in again with your administrator email."),
        "code": "password_reset_completed",
        "revoked_sessions": 2,
    }

    assert "fantasy_session" not in client.cookies

    with SessionFactory() as session:
        administrator = session.get(
            User,
            administrator_id,
        )

        assert administrator is not None
        assert verify_password(
            new_password,
            administrator.password_hash,
        )
        assert not verify_password(
            old_password,
            administrator.password_hash,
        )

        repository = SessionRepository(session)

        assert repository.resolve_session(first_session.session_token) is None
        assert repository.resolve_session(second_session.session_token) is None

        active_sessions = session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == administrator_id,
                AuthSession.revoked_at.is_(None),
            )
        ).all()

    assert active_sessions == []


def test_completed_reset_token_is_single_use(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, _, _ = active_administrator
    sender = CapturingPasswordResetEmailSender()

    app.dependency_overrides[get_transactional_email_sender] = lambda: sender

    request_response = request_password_reset(
        client,
        email=email,
    )

    assert request_response.status_code == (status.HTTP_202_ACCEPTED)

    raw_token = sender.password_reset_messages[0]["password_reset_token"]

    first = complete_password_reset(
        client,
        token=raw_token,
        new_password="replacement horse battery staple",
    )
    second = complete_password_reset(
        client,
        token=raw_token,
        new_password="another replacement password",
    )

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert second.json()["code"] == "invalid_password_reset"
