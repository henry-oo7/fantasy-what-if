from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.auth_dependencies import get_current_user
from app.auth.security import verify_password
from app.core.settings import get_settings
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
from app.db.platform_models import BackgroundJob, Installation, TransactionalEmailDelivery
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import (
    AuthenticationRepository,
)
from app.repositories.session_repository import (
    SessionRepository,
)
from app.services.identity_email import password_changed_key, password_reset_key
from tests.support.identity_email import (
    PASSWORD_CHANGED,
    PASSWORD_RESET,
    deliver_and_extract_token,
    email_jobs_for,
    forbid_provider_and_network,
    identity_deliveries,
    issue_password_reset_token,
    token_count,
)

TRUSTED_ORIGIN = "http://localhost:5173"


RESET_ACCEPTED_BODY = {
    "detail": (
        "If an eligible administrator account exists for that email "
        "address, password reset instructions will be sent. Check your "
        "email for the next step."
    ),
    "code": "password_reset_request_accepted",
}


@pytest.fixture(autouse=True)
def no_provider_or_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return forbid_provider_and_network(monkeypatch)


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
            get_settings,
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


def reset_state(administrator_id: int) -> tuple[int, int, int]:
    """Return (reset deliveries, their jobs, tokens) for one administrator."""

    with SessionFactory() as session:
        deliveries = identity_deliveries(session, administrator_id, PASSWORD_RESET)

        return (
            len(deliveries),
            len(email_jobs_for(session, [delivery.id for delivery in deliveries])),
            token_count(session, administrator_id),
        )


def set_administrator_state(
    administrator_id: int,
    *,
    verified: bool = True,
    active: bool = True,
) -> None:
    with SessionFactory() as session:
        administrator = session.get(User, administrator_id)

        assert administrator is not None

        administrator.email_verified_at = datetime.now(UTC) if verified else None
        administrator.is_active = active
        session.commit()


def set_workspace_state(
    administrator_id: int,
    *,
    status: InstallationStatus,
    registration_status: WorkspaceRegistrationStatus,
    onboarding_stage: OnboardingStage,
) -> None:
    with SessionFactory() as session:
        administrator = session.get(User, administrator_id)

        assert administrator is not None

        workspace = session.get(Installation, administrator.workspace_id)

        assert workspace is not None

        workspace.status = status
        workspace.registration_status = registration_status
        workspace.onboarding_stage = onboarding_stage

        if registration_status is WorkspaceRegistrationStatus.DELETION_PENDING:
            requested_at = datetime.now(UTC)
            workspace.deletion_requested_at = requested_at
            workspace.deletion_scheduled_for = requested_at + timedelta(days=7)

        session.commit()


def test_eligible_administrator_queues_reset_delivery_without_token(
    client: TestClient,
    active_administrator: tuple[str, str, int],
    no_provider_or_network: list[str],
) -> None:
    email, _, administrator_id = active_administrator

    response = request_password_reset(
        client,
        email=f"  {email.upper()}  ",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == RESET_ACCEPTED_BODY

    with SessionFactory() as session:
        deliveries = identity_deliveries(session, administrator_id, PASSWORD_RESET)

        assert len(deliveries) == 1
        assert deliveries[0].idempotency_key == password_reset_key(administrator_id, 0)

        jobs = email_jobs_for(session, [deliveries[0].id])

        assert len(jobs) == 1
        assert jobs[0].payload == {
            "version": 1,
            "delivery_id": deliveries[0].id,
        }
        assert token_count(session, administrator_id) == 0

        delivery_id = deliveries[0].id

    raw_token = deliver_and_extract_token(delivery_id)

    assert raw_token not in response.text

    completion = complete_password_reset(
        client,
        token=raw_token,
        new_password="replacement horse battery staple",
    )

    assert completion.status_code == status.HTTP_200_OK
    assert no_provider_or_network == []


def test_unknown_email_receives_same_generic_response(
    client: TestClient,
) -> None:
    with SessionFactory() as session:
        before = (
            len(session.scalars(select(PasswordResetToken.id)).all()),
            len(session.scalars(select(TransactionalEmailDelivery.id)).all()),
            len(session.scalars(select(BackgroundJob.id)).all()),
        )

    response = request_password_reset(
        client,
        email=f"missing-{uuid4().hex}@example.com",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == RESET_ACCEPTED_BODY

    with SessionFactory() as session:
        after = (
            len(session.scalars(select(PasswordResetToken.id)).all()),
            len(session.scalars(select(TransactionalEmailDelivery.id)).all()),
            len(session.scalars(select(BackgroundJob.id)).all()),
        )

    assert after == before


@pytest.mark.parametrize(
    ("verified", "active", "workspace_state"),
    [
        pytest.param(True, False, None, id="inactive-administrator"),
        pytest.param(False, True, None, id="unverified-administrator"),
        pytest.param(
            True,
            True,
            (
                InstallationStatus.ACTIVE,
                WorkspaceRegistrationStatus.SUSPENDED,
                OnboardingStage.COMPLETE,
            ),
            id="suspended-workspace",
        ),
        pytest.param(
            True,
            True,
            (
                InstallationStatus.ACTIVE,
                WorkspaceRegistrationStatus.DELETED,
                OnboardingStage.COMPLETE,
            ),
            id="deleted-workspace",
        ),
        pytest.param(
            True,
            True,
            (
                InstallationStatus.SETUP_IN_PROGRESS,
                WorkspaceRegistrationStatus.ACTIVE,
                OnboardingStage.COMPLETE,
            ),
            id="installation-not-active",
        ),
        pytest.param(
            True,
            True,
            (
                InstallationStatus.SETUP_IN_PROGRESS,
                WorkspaceRegistrationStatus.PROVISIONAL,
                OnboardingStage.EMAIL_VERIFICATION_PENDING,
            ),
            id="pre-verification-onboarding",
        ),
    ],
)
def test_ineligible_reset_request_creates_nothing_and_matches_response(
    client: TestClient,
    active_administrator: tuple[str, str, int],
    verified: bool,
    active: bool,
    workspace_state: tuple[InstallationStatus, WorkspaceRegistrationStatus, OnboardingStage] | None,
) -> None:
    email, _, administrator_id = active_administrator

    set_administrator_state(administrator_id, verified=verified, active=active)

    if workspace_state is not None:
        set_workspace_state(
            administrator_id,
            status=workspace_state[0],
            registration_status=workspace_state[1],
            onboarding_stage=workspace_state[2],
        )

    response = request_password_reset(client, email=email)

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == RESET_ACCEPTED_BODY
    assert reset_state(administrator_id) == (0, 0, 0)

    with SessionFactory() as session:
        assert identity_deliveries(session, administrator_id) == []


@pytest.mark.parametrize(
    ("status_value", "registration_status", "onboarding_stage"),
    [
        pytest.param(
            InstallationStatus.ACTIVE,
            WorkspaceRegistrationStatus.DELETION_PENDING,
            OnboardingStage.COMPLETE,
            id="deletion-pending",
        ),
        pytest.param(
            InstallationStatus.SETUP_IN_PROGRESS,
            WorkspaceRegistrationStatus.PROVISIONAL,
            OnboardingStage.PROVIDER_SETUP_PENDING,
            id="resumable-onboarding",
        ),
    ],
)
def test_reset_request_is_eligible_for_deletion_pending_and_resumable_onboarding(
    client: TestClient,
    active_administrator: tuple[str, str, int],
    status_value: InstallationStatus,
    registration_status: WorkspaceRegistrationStatus,
    onboarding_stage: OnboardingStage,
) -> None:
    email, _, administrator_id = active_administrator

    set_workspace_state(
        administrator_id,
        status=status_value,
        registration_status=registration_status,
        onboarding_stage=onboarding_stage,
    )

    response = request_password_reset(client, email=email)

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == RESET_ACCEPTED_BODY
    assert reset_state(administrator_id) == (1, 1, 0)


def test_reset_request_is_generic_when_transactional_email_is_disabled(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, _, administrator_id = active_administrator

    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"transactional_email_enabled": False}
    )

    response = request_password_reset(
        client,
        email=email,
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == RESET_ACCEPTED_BODY
    assert "email_delivery_unavailable" not in response.text
    assert reset_state(administrator_id)[:2] == (1, 1)


def test_new_reset_request_does_not_invalidate_prior_token(
    client: TestClient,
    active_administrator: tuple[str, str, int],
) -> None:
    email, _, administrator_id = active_administrator

    with SessionFactory() as session:
        prior_token = issue_password_reset_token(
            session,
            user=session.get(User, administrator_id),
            lifetime_minutes=30,
        ).raw_token
        session.commit()

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
    assert first.json() == second.json() == RESET_ACCEPTED_BODY

    # While the first delivery is pending the second request reuses it.
    assert reset_state(administrator_id) == (1, 1, 1)

    first_completion = complete_password_reset(
        client,
        token=prior_token,
        new_password="replacement horse battery staple",
    )

    assert first_completion.status_code == status.HTTP_200_OK


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

    with SessionFactory() as session:
        reset_deliveries = identity_deliveries(session, administrator_id, PASSWORD_RESET)

        assert len(reset_deliveries) == 1

        reset_delivery_id = reset_deliveries[0].id

    raw_token = deliver_and_extract_token(reset_delivery_id)
    tokens_before_completion = reset_state(administrator_id)[2]
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

    # Completion queues exactly one tokenless password-changed notice and
    # creates no token.
    assert reset_state(administrator_id)[2] == tokens_before_completion

    with SessionFactory() as session:
        used_token = session.scalar(
            select(PasswordResetToken).where(PasswordResetToken.user_id == administrator_id)
        )

        assert used_token is not None

        changed = identity_deliveries(session, administrator_id, PASSWORD_CHANGED)

        assert len(changed) == 1
        assert changed[0].idempotency_key == password_changed_key(administrator_id, used_token.id)
        assert len(email_jobs_for(session, [changed[0].id])) == 1

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
    email, _, administrator_id = active_administrator

    request_response = request_password_reset(
        client,
        email=email,
    )

    assert request_response.status_code == (status.HTTP_202_ACCEPTED)

    with SessionFactory() as session:
        reset_delivery_id = identity_deliveries(session, administrator_id, PASSWORD_RESET)[0].id

    raw_token = deliver_and_extract_token(reset_delivery_id)

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

    with SessionFactory() as session:
        changed = identity_deliveries(session, administrator_id, PASSWORD_CHANGED)

        assert len(changed) == 1
        assert len(email_jobs_for(session, [changed[0].id])) == 1
