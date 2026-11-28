import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api.auth_dependencies import get_current_user
from app.core.settings import get_settings
from app.db.auth_models import (
    AuthSession,
    EmailVerificationToken,
    RegistrationRateLimit,
    User,
    UserRole,
)
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    RegistrationMode,
    TransactionalEmailScope,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
    TransactionalEmailDelivery,
)
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import AuthenticationRepository
from app.services.identity_email import (
    queue_registration_verification,
    registration_verification_key,
    verification_resend_key,
)
from tests.support.identity_email import (
    VERIFICATION,
    delete_users,
    deliver_and_extract_token,
    email_jobs_for,
    forbid_provider_and_network,
    identity_deliveries,
    issue_email_verification_token,
    token_count,
)

TRUSTED_ORIGIN = "http://localhost:5173"


@pytest.fixture(autouse=True)
def no_provider_or_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return forbid_provider_and_network(monkeypatch)


@pytest.fixture(autouse=True)
def isolate_registration_rate_limit_rows() -> Iterator[None]:
    """Remove rate-limit rows created by each test.

    API tests share one TestClient source address, so the source-scoped
    registration_submission bucket would otherwise leak across tests.
    """

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
    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.pop(
        get_settings,
        None,
    )


def issue_csrf(
    client: TestClient,
) -> str:
    response = client.get("/api/auth/csrf")

    assert response.status_code == status.HTTP_200_OK

    return response.json()["csrf_token"]


def registration_payload() -> dict[str, str]:
    suffix = uuid4().hex[:12]

    return {
        "display_name": "API Account Holder",
        "email": f"api-register-{suffix}@example.com",
        "password": "correct horse battery staple",
        "confirm_password": "correct horse battery staple",
    }


def open_registration_settings():
    settings = get_settings().model_copy(
        update={
            "registration_mode": RegistrationMode.OPEN,
        }
    )

    return settings


def delete_registration(
    *,
    email: str,
) -> None:
    with SessionFactory() as session:
        user = session.scalar(select(User).where(User.normalized_email == email))

        if user is None:
            return

        workspace_id = user.workspace_id
        delete_users(session, [user.id])
        session.flush()

        if workspace_id is not None:
            workspace = session.get(
                Installation,
                workspace_id,
            )

            if workspace is not None:
                session.delete(workspace)

        session.commit()


def test_registration_requires_pre_auth_csrf(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/registration",
        headers={
            "Origin": TRUSTED_ORIGIN,
        },
        json=registration_payload(),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "CSRF validation failed.",
    }


def test_registration_rejects_untrusted_origin(
    client: TestClient,
) -> None:
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/registration",
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
        json=registration_payload(),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "Request origin is not allowed.",
    }


def test_closed_registration_returns_stable_error(
    client: TestClient,
) -> None:
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/registration",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json=registration_payload(),
    )

    assert response.status_code == (status.HTTP_503_SERVICE_UNAVAILABLE)
    assert response.json() == {
        "detail": "Registration is unavailable.",
        "code": "registration_unavailable",
        "retryable": False,
    }


def test_open_registration_commits_before_email_delivery(
    client: TestClient,
    no_provider_or_network: list[str],
) -> None:
    payload = registration_payload()

    app.dependency_overrides[get_settings] = open_registration_settings

    csrf_token = issue_csrf(client)

    try:
        with SessionFactory() as session:
            owned_rows_before = personal_account_side_rows(session)

        response = client.post(
            "/api/registration",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token,
            },
            json=payload,
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"verification_required": True}

        serialized = response.text

        assert payload["password"] not in serialized
        assert "verification_token" not in serialized
        assert "username" not in serialized
        assert "workspace" not in serialized

        with SessionFactory() as session:
            user = session.scalar(select(User).where(User.normalized_email == payload["email"]))

            assert user is not None
            assert user.workspace_id is None
            assert user.role is None
            assert user.username is None
            assert user.email_verified_at is None
            assert user.display_name == payload["display_name"]
            assert personal_account_side_rows(session) == owned_rows_before

            deliveries = identity_deliveries(session, user.id)

            assert len(deliveries) == 1
            assert deliveries[0].event_type is VERIFICATION
            assert deliveries[0].scope is TransactionalEmailScope.UNSCOPED
            assert deliveries[0].workspace_id is None
            assert deliveries[0].user_id == user.id

            jobs = email_jobs_for(session, [deliveries[0].id])

            assert len(jobs) == 1
            assert jobs[0].payload == {
                "version": 1,
                "delivery_id": deliveries[0].id,
            }
            assert token_count(session, user.id) == 0

        assert no_provider_or_network == []
    finally:
        delete_registration(
            email=payload["email"],
        )


def personal_account_side_rows(session) -> tuple[int, int, int]:
    """Return global (installations, provider connections, claims) counts."""

    return (
        int(session.scalar(select(func.count(Installation.id))) or 0),
        int(session.scalar(select(func.count(ProviderConnection.id))) or 0),
        int(session.scalar(select(func.count(ProviderLeagueClaim.id))) or 0),
    )


def test_registration_ignores_legacy_workspace_fields(
    client: TestClient,
) -> None:
    payload = registration_payload() | {
        "workspace_name": "Ignored League",
        "workspace_slug": f"ignored-{uuid4().hex[:12]}",
    }

    app.dependency_overrides[get_settings] = open_registration_settings

    try:
        response = post_registration(client, payload)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"verification_required": True}

        with SessionFactory() as session:
            user = session.scalar(select(User).where(User.normalized_email == payload["email"]))

            assert user is not None
            assert user.workspace_id is None
            assert (
                session.scalar(
                    select(func.count(Installation.id)).where(
                        Installation.normalized_slug == payload["workspace_slug"]
                    )
                )
                == 0
            )
    finally:
        delete_registration(email=payload["email"])


def test_registration_rejects_mismatched_password_confirmation(
    client: TestClient,
) -> None:
    payload = registration_payload() | {"confirm_password": "a different passphrase!"}

    app.dependency_overrides[get_settings] = open_registration_settings

    try:
        response = post_registration(client, payload)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

        with SessionFactory() as session:
            assert (
                session.scalar(select(User).where(User.normalized_email == payload["email"]))
                is None
            )
    finally:
        delete_registration(email=payload["email"])


def test_registration_succeeds_when_transactional_email_is_disabled(
    client: TestClient,
) -> None:
    payload = registration_payload()

    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={
            "registration_mode": RegistrationMode.OPEN,
            "transactional_email_enabled": False,
        }
    )

    csrf_token = issue_csrf(client)

    try:
        response = client.post(
            "/api/registration",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token,
            },
            json=payload,
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"verification_required": True}
        assert "email_delivery_unavailable" not in response.text

        with SessionFactory() as session:
            user = session.scalar(select(User).where(User.normalized_email == payload["email"]))

            assert user is not None
            assert len(identity_deliveries(session, user.id)) == 1
    finally:
        delete_registration(
            email=payload["email"],
        )


def test_registration_response_never_exposes_internal_identifiers(
    client: TestClient,
) -> None:
    payload = registration_payload()

    app.dependency_overrides[get_settings] = open_registration_settings

    csrf_token = issue_csrf(client)

    try:
        response = client.post(
            "/api/registration",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token,
            },
            json=payload,
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert set(response.json()) == {"verification_required"}
    finally:
        delete_registration(
            email=payload["email"],
        )


def register_for_verification(
    client: TestClient,
) -> tuple[dict[str, str], str]:
    payload = registration_payload()

    app.dependency_overrides[get_settings] = open_registration_settings

    csrf_token = issue_csrf(client)
    response = client.post(
        "/api/registration",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json=payload,
    )

    assert response.status_code == status.HTTP_201_CREATED

    with SessionFactory() as session:
        user = session.scalar(select(User).where(User.normalized_email == payload["email"]))

        assert user is not None

        deliveries = identity_deliveries(session, user.id, VERIFICATION)

        assert len(deliveries) == 1

        delivery_id = deliveries[0].id

    return (
        payload,
        deliver_and_extract_token(delivery_id),
    )


def test_email_verification_requires_pre_auth_csrf(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/registration/verify-email",
        headers={
            "Origin": TRUSTED_ORIGIN,
        },
        json={
            "token": "synthetic-verification-token",
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "CSRF validation failed.",
    }


def test_email_verification_rejects_untrusted_origin(
    client: TestClient,
) -> None:
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/registration/verify-email",
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
        json={
            "token": "synthetic-verification-token",
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "detail": "Request origin is not allowed.",
    }


def test_email_verification_creates_personal_session(
    client: TestClient,
) -> None:
    payload, verification_token = register_for_verification(client)

    csrf_token = issue_csrf(client)

    try:
        response = client.post(
            "/api/registration/verify-email",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token,
            },
            json={
                "token": verification_token,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "display_name": payload["display_name"],
            "email": payload["email"],
            "email_verified": True,
            "navigation_state": "import_required",
            "csrf_token": response.json()["csrf_token"],
        }

        assert response.json()["csrf_token"]
        assert "username" not in response.json()
        assert "administrator_id" not in response.json()
        assert "verification_token" not in response.json()
        assert verification_token not in response.text

        assert "fantasy_session" in client.cookies
        assert "fantasy_session_csrf" in client.cookies
        assert "fantasy_preauth_csrf" not in client.cookies

        with SessionFactory() as session:
            account = session.scalar(select(User).where(User.normalized_email == payload["email"]))

            assert account is not None
            assert account.email_verified_at is not None
            assert account.workspace_id is None
            assert account.role is None
    finally:
        delete_registration(
            email=payload["email"],
        )


def test_email_verification_for_legacy_provisional_administrator(
    client: TestClient,
) -> None:
    payload, verification_token = committed_provisional_registration()

    try:
        response = post_verification(client, verification_token)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "display_name": payload["display_name"],
            "email": payload["email"],
            "email_verified": True,
            "navigation_state": "legacy_workspace",
            "csrf_token": response.json()["csrf_token"],
        }
        assert verification_token not in response.text

        with SessionFactory() as session:
            administrator = session.scalar(
                select(User).where(User.normalized_email == payload["email"])
            )

            assert administrator is not None
            assert administrator.email_verified_at is not None
            assert administrator.workspace_id is not None

            workspace = session.get(Installation, administrator.workspace_id)

            assert workspace is not None
            assert workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
            assert workspace.onboarding_stage is OnboardingStage.PROVIDER_SETUP_PENDING
    finally:
        delete_registration(email=payload["email"])


def test_email_verification_token_is_single_use_at_api_boundary(
    client: TestClient,
) -> None:
    payload, verification_token = register_for_verification(client)

    first_csrf = issue_csrf(client)

    try:
        first = client.post(
            "/api/registration/verify-email",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": first_csrf,
            },
            json={
                "token": verification_token,
            },
        )

        assert first.status_code == status.HTTP_200_OK

        client.cookies.clear()
        second_csrf = issue_csrf(client)

        second = client.post(
            "/api/registration/verify-email",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": second_csrf,
            },
            json={
                "token": verification_token,
            },
        )

        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert second.json() == {
            "detail": ("The email verification link is invalid or expired."),
            "code": "invalid_email_verification",
            "retryable": False,
        }
    finally:
        delete_registration(
            email=payload["email"],
        )


def test_invalid_email_verification_returns_stable_error(
    client: TestClient,
) -> None:
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/registration/verify-email",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "token": "invalid-verification-token",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": ("The email verification link is invalid or expired."),
        "code": "invalid_email_verification",
        "retryable": False,
    }


def test_verified_personal_session_cannot_access_workspace_analytics(
    client: TestClient,
) -> None:
    payload, verification_token = register_for_verification(client)

    csrf_token = issue_csrf(client)

    try:
        verification = client.post(
            "/api/registration/verify-email",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token,
            },
            json={
                "token": verification_token,
            },
        )

        assert verification.status_code == status.HTTP_200_OK

        inherited_user_override = app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        try:
            analytics_response = client.get("/api/analytics/all-play")
        finally:
            if inherited_user_override is not None:
                app.dependency_overrides[get_current_user] = inherited_user_override

        assert analytics_response.status_code == (status.HTTP_403_FORBIDDEN)
        assert analytics_response.json() == {
            "detail": "Workspace access is unavailable.",
            "code": "workspace_inactive",
            "retryable": False,
        }
    finally:
        delete_registration(
            email=payload["email"],
        )


def resend_verification(
    client: TestClient,
    *,
    email: str,
):
    csrf_token = issue_csrf(client)

    return client.post(
        "/api/registration/resend-verification",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "email": email,
        },
    )


def test_verification_resend_requires_pre_auth_csrf(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/registration/resend-verification",
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


def test_verification_resend_rejects_untrusted_origin(
    client: TestClient,
) -> None:
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/registration/resend-verification",
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


RESEND_ACCEPTED_BODY = {
    "detail": (
        "If an eligible account exists for that email "
        "address, verification instructions will be sent. Check your "
        "email for the next step."
    ),
    "code": "verification_resend_accepted",
}


def test_registration_then_resend_reuses_pending_delivery(
    client: TestClient,
) -> None:
    payload = registration_payload()

    app.dependency_overrides[get_settings] = open_registration_settings

    try:
        assert post_registration(client, payload).status_code == status.HTTP_201_CREATED

        response = resend_verification(client, email=payload["email"])

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == RESEND_ACCEPTED_BODY

        with SessionFactory() as session:
            user = session.scalar(select(User).where(User.normalized_email == payload["email"]))
            assert user is not None
            deliveries = identity_deliveries(session, user.id, VERIFICATION)

            assert len(deliveries) == 1
            assert deliveries[0].idempotency_key == registration_verification_key(user.id)
            assert len(email_jobs_for(session, [deliveries[0].id])) == 1
            assert token_count(session, user.id) == 0
    finally:
        delete_registration(email=payload["email"])


def test_verification_resend_does_not_invalidate_prior_token(
    client: TestClient,
    no_provider_or_network: list[str],
) -> None:
    payload, original_token = register_for_verification(client)

    try:
        with SessionFactory() as session:
            user = session.scalar(select(User).where(User.normalized_email == payload["email"]))
            assert user is not None
            user_id = user.id
            first = identity_deliveries(session, user_id, VERIFICATION)[0]
            first_id = first.id
            tokens_before = token_count(session, user_id)

        assert tokens_before == 1

        response = resend_verification(
            client,
            email=payload["email"],
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == RESEND_ACCEPTED_BODY
        assert original_token not in response.text

        with SessionFactory() as session:
            deliveries = identity_deliveries(session, user_id, VERIFICATION)

            assert [delivery.id for delivery in deliveries][0] == first_id
            assert len(deliveries) == 2
            assert deliveries[1].idempotency_key == verification_resend_key(user_id, first_id)
            assert len(email_jobs_for(session, [deliveries[1].id])) == 1
            # The route never creates a token.
            assert token_count(session, user_id) == tokens_before
            second_id = deliveries[1].id

        # A second request while the resend delivery is pending reuses it.
        repeated = resend_verification(
            client,
            email=payload["email"],
        )

        assert repeated.status_code == status.HTTP_202_ACCEPTED
        assert repeated.json() == RESEND_ACCEPTED_BODY

        with SessionFactory() as session:
            deliveries = identity_deliveries(session, user_id, VERIFICATION)

            assert [delivery.id for delivery in deliveries] == [first_id, second_id]
            assert len(email_jobs_for(session, [first_id, second_id])) == 2
            assert token_count(session, user_id) == tokens_before

        client.cookies.clear()
        original_response = post_verification(client, original_token)

        assert original_response.status_code == status.HTTP_200_OK
        assert no_provider_or_network == []
    finally:
        delete_registration(
            email=payload["email"],
        )


def test_unknown_email_receives_same_resend_response(
    client: TestClient,
) -> None:
    before = global_email_row_counts()

    response = resend_verification(
        client,
        email=f"missing-{uuid4().hex}@example.com",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == RESEND_ACCEPTED_BODY
    assert global_email_row_counts() == before


def global_email_row_counts() -> tuple[int, int, int]:
    with SessionFactory() as session:
        return (
            int(session.scalar(select(func.count()).select_from(TransactionalEmailDelivery)) or 0),
            int(session.scalar(select(func.count()).select_from(BackgroundJob)) or 0),
            int(session.scalar(select(func.count()).select_from(EmailVerificationToken)) or 0),
        )


def test_verified_email_receives_same_resend_response(
    client: TestClient,
) -> None:
    payload, verification_token = register_for_verification(client)

    csrf_token = issue_csrf(client)

    try:
        verification = client.post(
            "/api/registration/verify-email",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token,
            },
            json={
                "token": verification_token,
            },
        )

        assert verification.status_code == status.HTTP_200_OK
        before = resend_state(payload["email"])

        client.cookies.clear()
        response = resend_verification(
            client,
            email=payload["email"],
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == RESEND_ACCEPTED_BODY
        assert resend_state(payload["email"]) == before
    finally:
        delete_registration(
            email=payload["email"],
        )


def resend_state(email: str) -> tuple[int, int, int]:
    """Return (deliveries, jobs, tokens) committed for one administrator."""

    with SessionFactory() as session:
        user = session.scalar(select(User).where(User.normalized_email == email))

        assert user is not None

        deliveries = identity_deliveries(session, user.id)

        return (
            len(deliveries),
            len(email_jobs_for(session, [delivery.id for delivery in deliveries])),
            token_count(session, user.id),
        )


def mark_administrator(email: str, *, verified: bool, active: bool) -> None:
    with SessionFactory() as session:
        user = session.scalar(select(User).where(User.normalized_email == email))

        assert user is not None

        if verified:
            user.email_verified_at = datetime.now(UTC)

        user.is_active = active
        session.commit()


@pytest.mark.parametrize(
    ("verified", "active", "workspace_status", "onboarding_stage"),
    [
        pytest.param(
            True,
            True,
            WorkspaceRegistrationStatus.PROVISIONAL,
            OnboardingStage.EMAIL_VERIFICATION_PENDING,
            id="verified-admin",
        ),
        pytest.param(
            False,
            False,
            WorkspaceRegistrationStatus.PROVISIONAL,
            OnboardingStage.EMAIL_VERIFICATION_PENDING,
            id="inactive-admin",
        ),
        pytest.param(
            False,
            True,
            WorkspaceRegistrationStatus.ACTIVE,
            OnboardingStage.EMAIL_VERIFICATION_PENDING,
            id="workspace-not-provisional",
        ),
        pytest.param(
            False,
            True,
            WorkspaceRegistrationStatus.PROVISIONAL,
            OnboardingStage.PROVIDER_SETUP_PENDING,
            id="stage-not-email-verification-pending",
        ),
    ],
)
def test_ineligible_legacy_resend_creates_nothing_and_returns_identical_response(
    client: TestClient,
    verified: bool,
    active: bool,
    workspace_status: WorkspaceRegistrationStatus,
    onboarding_stage: OnboardingStage,
) -> None:
    payload, _ = committed_provisional_registration(
        workspace_status=workspace_status,
        onboarding_stage=onboarding_stage,
    )

    try:
        mark_administrator(payload["email"], verified=verified, active=active)
        before = resend_state(payload["email"])

        response = resend_verification(client, email=payload["email"])

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == RESEND_ACCEPTED_BODY
        assert resend_state(payload["email"]) == before
        # The registration delivery and its job are the only rows.
        assert before[:2] == (1, 1)
    finally:
        delete_registration(email=payload["email"])


@pytest.mark.parametrize(
    ("verified", "active"),
    [
        pytest.param(True, True, id="verified-personal"),
        pytest.param(False, False, id="inactive-personal"),
    ],
)
def test_ineligible_personal_resend_creates_nothing_and_returns_identical_response(
    client: TestClient,
    verified: bool,
    active: bool,
) -> None:
    payload = registration_payload()
    app.dependency_overrides[get_settings] = open_registration_settings

    try:
        assert post_registration(client, payload).status_code == status.HTTP_201_CREATED

        mark_administrator(payload["email"], verified=verified, active=active)
        before = resend_state(payload["email"])

        response = resend_verification(client, email=payload["email"])

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == RESEND_ACCEPTED_BODY
        assert resend_state(payload["email"]) == before
        assert before[:2] == (1, 1)
    finally:
        delete_registration(email=payload["email"])


def test_verification_resend_is_limited_after_three_requests(
    client: TestClient,
) -> None:
    email = f"limited-{uuid4().hex}@example.com"

    responses = [
        resend_verification(
            client,
            email=email,
        )
        for _ in range(4)
    ]

    for response in responses[:3]:
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json()["code"] == ("verification_resend_accepted")

    blocked = responses[3]

    assert blocked.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)
    assert blocked.json() == {
        "detail": ("Verification resend is temporarily unavailable. Try again later."),
        "code": "verification_resend_throttled",
        "retryable": True,
    }

    retry_after = int(blocked.headers["Retry-After"])

    assert 1 <= retry_after <= 60 * 60


def test_resend_is_generic_when_transactional_email_is_disabled(
    client: TestClient,
) -> None:
    payload, _ = register_for_verification(client)

    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"transactional_email_enabled": False}
    )

    try:
        response = resend_verification(
            client,
            email=payload["email"],
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == RESEND_ACCEPTED_BODY
        assert "email_delivery_unavailable" not in response.text
    finally:
        delete_registration(
            email=payload["email"],
        )


def post_registration(
    client: TestClient,
    payload: dict[str, str],
    csrf_token: str | None = None,
):
    return client.post(
        "/api/registration",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token or issue_csrf(client),
        },
        json=payload,
    )


def registration_row_counts(
    *,
    email: str,
) -> tuple[int, int, int, int]:
    """Return (users, tokens, deliveries, jobs) for a registration."""

    with SessionFactory() as session:
        users = session.scalars(select(User).where(User.normalized_email == email)).all()
        user_ids = [user.id for user in users]
        token_total = sum(token_count(session, user_id) for user_id in user_ids)
        delivery_ids = [
            delivery.id
            for user_id in user_ids
            for delivery in identity_deliveries(session, user_id)
        ]
        job_total = len(email_jobs_for(session, delivery_ids))

    return len(user_ids), token_total, len(delivery_ids), job_total


def test_duplicate_email_registration_is_indistinguishable(
    client: TestClient,
) -> None:
    first = registration_payload()
    second = registration_payload()
    second["email"] = first["email"].upper()

    app.dependency_overrides[get_settings] = open_registration_settings

    try:
        created = post_registration(client, first)
        assert created.status_code == status.HTTP_201_CREATED
        before = registration_row_counts(email=first["email"])
        assert before == (1, 0, 1, 1)

        duplicate = post_registration(client, second)

        assert duplicate.status_code == created.status_code
        assert duplicate.headers["content-type"] == created.headers["content-type"]
        assert duplicate.json() == created.json() == {"verification_required": True}
        assert registration_row_counts(email=first["email"]) == before
    finally:
        delete_registration(email=first["email"])


def test_concurrent_duplicate_email_registrations_create_one_identity(
    client: TestClient,
) -> None:
    del client
    email = registration_payload()["email"]
    payloads = [registration_payload() for _ in range(2)]

    for payload in payloads:
        payload["email"] = email

    app.dependency_overrides[get_settings] = open_registration_settings

    browsers = [TestClient(app) for _ in payloads]
    barrier = threading.Barrier(len(payloads))

    def submit(index: int):
        browser = browsers[index]
        csrf_token = issue_csrf(browser)
        barrier.wait(timeout=10)

        return post_registration(browser, payloads[index], csrf_token)

    try:
        with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
            responses = list(executor.map(submit, range(len(payloads))))

        assert [response.status_code for response in responses] == [
            status.HTTP_201_CREATED,
            status.HTTP_201_CREATED,
        ]

        for response in responses:
            assert response.json() == {"verification_required": True}

        assert registration_row_counts(email=email) == (1, 0, 1, 1)
    finally:
        for browser in browsers:
            browser.close()

        delete_registration(email=email)


def test_registration_submissions_are_throttled_per_source(
    client: TestClient,
) -> None:
    payloads = [registration_payload() for _ in range(6)]

    app.dependency_overrides[get_settings] = open_registration_settings

    try:
        for payload in payloads[:5]:
            assert post_registration(client, payload).status_code == status.HTTP_201_CREATED

        over_limit = payloads[5]
        response = post_registration(client, over_limit)

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert int(response.headers["Retry-After"]) > 0
        assert response.json() == {
            "detail": "Registration is temporarily unavailable. Try again later.",
            "code": "registration_throttled",
            "retryable": True,
        }
        assert registration_row_counts(email=over_limit["email"]) == (0, 0, 0, 0)
    finally:
        for payload in payloads:
            delete_registration(email=payload["email"])


def test_registration_throttle_applies_before_mode_and_validation(
    client: TestClient,
) -> None:
    # Closed mode and invalid input still count against the source bucket,
    # so the limit is enforced before any lookup or state creation.
    for _ in range(5):
        response = post_registration(client, registration_payload())
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    response = post_registration(client, registration_payload())

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def committed_provisional_registration(
    *,
    workspace_status: WorkspaceRegistrationStatus = WorkspaceRegistrationStatus.PROVISIONAL,
    onboarding_stage: OnboardingStage = OnboardingStage.EMAIL_VERIFICATION_PENDING,
) -> tuple[dict[str, str], str]:
    """Seed a legacy provisional administrator (no longer producible by the API)."""

    payload = registration_payload()
    slug = f"api-legacy-{uuid4().hex[:12]}"

    with SessionFactory() as session:
        workspace = Installation(
            application_name="API Legacy League",
            slug=slug,
            normalized_slug=slug,
            timezone="America/Chicago",
            status=InstallationStatus.SETUP_IN_PROGRESS,
            registration_status=workspace_status,
            onboarding_stage=onboarding_stage,
        )
        session.add(workspace)
        session.flush()
        administrator = AuthenticationRepository(session).create_user(
            username=f"internal-{uuid4().hex[:12]}",
            display_name=payload["display_name"],
            email=payload["email"],
            password=payload["password"],
            role=UserRole.ADMIN,
            workspace_id=workspace.id,
        )
        queue_registration_verification(
            session, user_id=administrator.id, workspace_id=workspace.id
        )
        session.flush()
        issued = issue_email_verification_token(
            session,
            user=administrator,
            lifetime_hours=24,
        )
        session.commit()

    return payload, issued.raw_token


def verification_state(email: str) -> tuple[object, ...]:
    with SessionFactory() as session:
        user = session.scalar(select(User).where(User.normalized_email == email))
        assert user is not None
        workspace = session.get(Installation, user.workspace_id)
        assert workspace is not None
        tokens = session.scalars(
            select(EmailVerificationToken.used_at).where(EmailVerificationToken.user_id == user.id)
        ).all()
        sessions = session.scalars(select(AuthSession.id).where(AuthSession.user_id == user.id))

        return (
            user.email_verified_at,
            workspace.registration_status,
            workspace.onboarding_stage,
            tuple(tokens),
            len(sessions.all()),
        )


def post_verification(client: TestClient, token: str, csrf_token: str | None = None):
    return client.post(
        "/api/registration/verify-email",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token or issue_csrf(client),
        },
        json={"token": token},
    )


@pytest.mark.parametrize(
    ("workspace_status", "onboarding_stage"),
    [
        (WorkspaceRegistrationStatus.ACTIVE, OnboardingStage.EMAIL_VERIFICATION_PENDING),
        (WorkspaceRegistrationStatus.SUSPENDED, OnboardingStage.EMAIL_VERIFICATION_PENDING),
        (WorkspaceRegistrationStatus.PROVISIONAL, OnboardingStage.PROVIDER_SETUP_PENDING),
    ],
)
def test_verification_outside_pending_provisional_state_is_rejected(
    client: TestClient,
    workspace_status: WorkspaceRegistrationStatus,
    onboarding_stage: OnboardingStage,
) -> None:
    payload, token = committed_provisional_registration(
        workspace_status=workspace_status,
        onboarding_stage=onboarding_stage,
    )

    try:
        before = verification_state(payload["email"])

        response = post_verification(client, token)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == {
            "detail": "Email verification is unavailable.",
            "code": "email_verification_unavailable",
            "retryable": False,
        }
        assert verification_state(payload["email"]) == before
        assert before[0] is None
        assert before[3] == (None,)
        assert before[4] == 0
    finally:
        delete_registration(email=payload["email"])


def test_concurrent_verification_of_one_token_creates_one_session(
    client: TestClient,
) -> None:
    del client
    payload, token = committed_provisional_registration()
    browsers = [TestClient(app) for _ in range(2)]
    barrier = threading.Barrier(len(browsers))

    def verify(index: int):
        browser = browsers[index]
        csrf_token = issue_csrf(browser)
        barrier.wait(timeout=10)

        return post_verification(browser, token, csrf_token)

    try:
        with ThreadPoolExecutor(max_workers=len(browsers)) as executor:
            responses = list(executor.map(verify, range(len(browsers))))

        assert sorted(response.status_code for response in responses) == [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

        with SessionFactory() as session:
            user = session.scalar(select(User).where(User.normalized_email == payload["email"]))
            assert user is not None
            active = session.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user.id,
                    AuthSession.revoked_at.is_(None),
                )
            ).all()

        assert len(active) == 1
    finally:
        for browser in browsers:
            browser.close()

        delete_registration(email=payload["email"])
