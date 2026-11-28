# ruff: noqa: F811  (imported pytest fixtures are used as test parameters)
"""Critical invariants of the account-only identity cutover (ADR 0010).

Real PostgreSQL, committed synthetic data, and the real API/worker. No
provider or network call is made. Races assert invariants that hold in
either serialization order, never timing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api.auth_dependencies import get_current_user
from app.auth.authentication import InvalidCredentialsError, log_in_by_email
from app.auth.identity import IdentityKind, classify_identity
from app.core.settings import get_settings
from app.db.auth_models import (
    AuthSession,
    AuthSessionPurpose,
    LoginAttempt,
    RegistrationRateLimit,
    SessionRevocationReason,
    User,
    UserRole,
)
from app.db.models import League
from app.db.platform_enums import (
    RegistrationMode,
    TransactionalEmailScope,
    UserSeasonAccessStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.db.session import SessionFactory
from app.main import app
from app.notifications.delivery_job_contract import transactional_email_job_idempotency_key
from app.repositories.auth_repository import AuthenticationRepository, DuplicateEmailError
from app.services.account_registration import register_personal_account
from app.services.identity_email import PASSWORD_CHANGED, VERIFICATION
from app.services.password_recovery import reset_password
from tests.support.identity_email import (
    delete_users,
    deliver_and_extract_token,
    deliver_identity_email,
    forbid_provider_and_network,
    identity_deliveries,
    issue_password_reset_token,
)
from tests.support.league_import import (  # noqa: F401 - fixtures
    Registry,
    create_caller,
    registry,
)
from tests.support.league_season_access import (  # noqa: F401 - fixtures
    get,
    grant,
    real_workspace_authentication,
    seed_season,
)
from tests.test_registration_concurrency import run_concurrently

TRUSTED_ORIGIN = "http://localhost:5173"
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "an entirely new synthetic passphrase"
GENERIC_LOGIN = {"detail": "Invalid email or password."}
ME_KEYS = {
    "authenticated",
    "display_name",
    "email",
    "email_verified",
    "navigation_state",
    "legacy_workspace",
    "session_purpose",
}


@pytest.fixture(autouse=True)
def no_provider_or_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return forbid_provider_and_network(monkeypatch)


@pytest.fixture(autouse=True)
def isolate_throttle_rows() -> Iterator[None]:
    with SessionFactory() as session:
        limits = set(session.scalars(select(RegistrationRateLimit.id)))
        attempts = set(session.scalars(select(LoginAttempt.id)))

    yield

    with SessionFactory() as session:
        session.execute(
            delete(RegistrationRateLimit).where(RegistrationRateLimit.id.not_in(limits))
        )
        session.execute(delete(LoginAttempt).where(LoginAttempt.id.not_in(attempts)))
        session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    inherited = app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"registration_mode": RegistrationMode.OPEN}
    )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    app.dependency_overrides.pop(get_settings, None)

    if inherited is not None:
        app.dependency_overrides[get_current_user] = inherited


@pytest.fixture
def created_users() -> Iterator[list[int]]:
    ids: list[int] = []
    yield ids

    with SessionFactory() as session:
        delete_users(session, ids)
        session.commit()


def pre_auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    return {"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": token}


def post(client: TestClient, path: str, body: dict[str, object]):
    return client.post(path, headers=pre_auth_headers(client), json=body)


def login(client: TestClient, email: str, password: str = PASSWORD):
    return post(client, "/api/auth/admin/login", {"email": email, "password": password})


def new_personal(
    created: list[int],
    *,
    verified: bool = True,
    password: str = PASSWORD,
    active: bool = True,
) -> tuple[int, str]:
    email = f"personal-{uuid4().hex[:12]}@example.com"

    with SessionFactory() as session:
        user = AuthenticationRepository(session).create_personal_user(
            display_name="Synthetic Personal", email=email, password=password
        )
        user.email_verified_at = datetime.now(UTC) if verified else None
        user.is_active = active
        session.commit()
        created.append(user.id)

        return user.id, email


def active_sessions(user_id: int) -> int:
    with SessionFactory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            )
            or 0
        )


def table_counts() -> dict[str, int]:
    with SessionFactory() as session:
        return {
            model.__name__: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for model in (Installation, ProviderLeagueClaim, ProviderConnection, League)
        }


# Registration ----------------------------------------------------------------


def test_registration_creates_one_personal_user_and_no_workspace_state(
    client: TestClient, created_users: list[int]
) -> None:
    email = f"register-{uuid4().hex[:12]}@example.com"
    before = table_counts()

    response = post(
        client,
        "/api/registration",
        {
            "display_name": "  New   Person ",
            "email": email.upper(),
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json() == {"verification_required": True}
    assert table_counts() == before

    with SessionFactory() as session:
        user = session.scalar(select(User).where(User.normalized_email == email))
        assert user is not None
        created_users.append(user.id)
        assert classify_identity(user) is IdentityKind.PERSONAL
        assert (user.workspace_id, user.role, user.username) == (None, None, None)
        assert user.display_name == "New Person"
        assert user.email == email and user.email_verified_at is None

        deliveries = identity_deliveries(session, user.id)
        assert [(d.event_type, d.scope, d.workspace_id) for d in deliveries] == [
            (VERIFICATION, TransactionalEmailScope.UNSCOPED, None)
        ]
        job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key
                == transactional_email_job_idempotency_key(deliveries[0].id)
            )
        )
        assert job is not None and job.workspace_id is None
        assert job.payload == {"version": 1, "delivery_id": deliveries[0].id}
        assert email not in str(job.payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"confirm_password": "a different synthetic phrase"},
        {"workspace_name": "x", "workspace_slug": "xyz"},
    ],
)
def test_registration_rejects_mismatched_confirmation_and_ignores_workspace_fields(
    client: TestClient, created_users: list[int], overrides: dict[str, str]
) -> None:
    email = f"register-{uuid4().hex[:12]}@example.com"
    body = {
        "display_name": "Person",
        "email": email,
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    } | overrides
    before = table_counts()

    response = post(client, "/api/registration", body)

    with SessionFactory() as session:
        user = session.scalar(select(User).where(User.normalized_email == email))
        if user is not None:
            created_users.append(user.id)
            assert (user.workspace_id, user.role, user.username) == (None, None, None)

    assert table_counts() == before
    if "confirm_password" in overrides:
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert user is None


def test_registration_is_closed_unless_the_mode_is_open(created_users: list[int]) -> None:
    email = f"closed-{uuid4().hex[:12]}@example.com"

    with SessionFactory() as session:
        with pytest.raises(Exception, match="unavailable"):
            register_personal_account(
                session,
                registration_mode=RegistrationMode.CLOSED,
                display_name="Closed",
                email=email,
                password=PASSWORD,
            )

        assert session.scalar(select(User).where(User.normalized_email == email)) is None


def test_concurrent_same_email_registration_creates_one_account_and_one_delivery(
    created_users: list[int],
) -> None:
    email = f"race-{uuid4().hex[:12]}@example.com"

    def register(_index: int) -> object:
        with SessionFactory() as session:
            result = register_personal_account(
                session,
                registration_mode=RegistrationMode.OPEN,
                display_name="Racer",
                email=email.upper() if _index % 2 else email,
                password=PASSWORD,
            )
            session.commit()
            return result.user.id

    results = run_concurrently(register, 6)
    winners = [r for r in results if isinstance(r, int)]
    created_users.extend(winners)

    assert len(winners) == 1, results
    with SessionFactory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(User).where(User.normalized_email == email)
            )
            == 1
        )
        assert len(identity_deliveries(session, winners[0])) == 1


def test_global_normalized_email_uniqueness_is_enforced_for_every_shape(
    created_users: list[int], registry: Registry
) -> None:
    _user_id, email = new_personal(created_users)
    admin = create_caller(registry)

    with SessionFactory() as session:
        with pytest.raises(DuplicateEmailError):
            AuthenticationRepository(session).create_personal_user(
                display_name="Dup", email=email.upper(), password=PASSWORD
            )
        admin_email = session.scalar(select(User.email).where(User.id == admin.user_id))

    with SessionFactory() as session, pytest.raises(DuplicateEmailError):
        AuthenticationRepository(session).create_personal_user(
            display_name="Dup", email=str(admin_email), password=PASSWORD
        )


# Login and sessions ------------------------------------------------------------


def test_personal_email_login_has_no_workspace_and_leaks_nothing(
    client: TestClient, created_users: list[int]
) -> None:
    user_id, email = new_personal(created_users)

    response = login(client, email.upper())

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert set(body["user"]) == {"display_name", "email", "email_verified", "is_active"}
    assert body["session_purpose"] == "standard"
    assert "username" not in response.text and "workspace" not in response.text
    assert "role" not in response.text

    me = client.get("/api/auth/me")
    assert me.status_code == status.HTTP_200_OK
    assert set(me.json()) == ME_KEYS
    assert me.json()["legacy_workspace"] is None
    assert me.json()["navigation_state"] == "import_required"
    assert active_sessions(user_id) == 1


def test_legacy_administrator_email_login_still_works(
    client: TestClient, registry: Registry
) -> None:
    caller = create_caller(registry)
    with SessionFactory() as session:
        email = session.scalar(select(User.email).where(User.id == caller.user_id))

    response = login(client, str(email))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["user"]["role"] == "admin"
    assert response.json()["user"]["workspace_id"] == caller.workspace_id
    me = client.get("/api/auth/me").json()
    assert me["navigation_state"] == "legacy_workspace"
    assert me["legacy_workspace"]["role"] == "admin"
    assert "username" not in str(me)


def test_spectators_cannot_log_in_by_email_even_with_an_email_on_the_row(
    client: TestClient, registry: Registry
) -> None:
    admin = create_caller(registry)
    spectator = create_caller(registry, role=UserRole.SPECTATOR, workspace_id=admin.workspace_id)
    email = f"spectator-{uuid4().hex[:12]}@example.com"

    with SessionFactory() as session:
        row = session.get(User, spectator.user_id)
        row.email = row.normalized_email = email
        row.email_verified_at = datetime.now(UTC)
        session.commit()

    for attempt in (email, f"nobody-{uuid4().hex[:8]}@example.com"):
        response = login(client, attempt)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == GENERIC_LOGIN


def test_login_failures_are_indistinguishable(client: TestClient, created_users: list[int]) -> None:
    _id, verified_email = new_personal(created_users)
    _id, unverified_email = new_personal(created_users, verified=False)
    _id, inactive_email = new_personal(created_users, active=False)
    outcomes = {
        "wrong password": login(client, verified_email, "not the right password"),
        "unknown": login(client, f"unknown-{uuid4().hex[:8]}@example.com"),
        "unverified": login(client, unverified_email),
        "inactive": login(client, inactive_email),
    }

    for name, response in outcomes.items():
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, name
        assert response.json() == GENERIC_LOGIN, name
        assert not response.cookies, name


def test_multiple_personal_sessions_coexist_and_none_is_marked_replaced(
    client: TestClient, created_users: list[int]
) -> None:
    user_id, email = new_personal(created_users)
    second = TestClient(app)

    with second:
        assert login(client, email).status_code == status.HTTP_200_OK
        assert login(second, email).status_code == status.HTTP_200_OK

        assert client.get("/api/auth/me").status_code == status.HTTP_200_OK
        assert second.get("/api/auth/me").status_code == status.HTTP_200_OK

    assert active_sessions(user_id) == 2
    with SessionFactory() as session:
        reasons = set(
            session.scalars(
                select(AuthSession.revocation_reason).where(AuthSession.user_id == user_id)
            )
        )
        assert SessionRevocationReason.REPLACED not in reasons


def test_personal_account_never_receives_a_deletion_recovery_session(
    created_users: list[int],
) -> None:
    user_id, email = new_personal(created_users)

    with SessionFactory() as session:
        result = log_in_by_email(
            session,
            email=email,
            password=PASSWORD,
            session_lifetime_hours=1,
        )
        session.commit()

    assert result.session_purpose is AuthSessionPurpose.STANDARD
    with SessionFactory() as session:
        purposes = set(
            session.scalars(select(AuthSession.purpose).where(AuthSession.user_id == user_id))
        )
    assert purposes == {AuthSessionPurpose.STANDARD}


def test_legacy_deletion_pending_administrator_keeps_the_recovery_session(
    registry: Registry,
) -> None:
    caller = create_caller(
        registry, registration_status=WorkspaceRegistrationStatus.DELETION_PENDING
    )
    with SessionFactory() as session:
        email = session.scalar(select(User.email).where(User.id == caller.user_id))
        result = log_in_by_email(
            session, email=str(email), password=PASSWORD, session_lifetime_hours=1
        )
        session.commit()

    assert result.session_purpose is AuthSessionPurpose.DELETION_RECOVERY
    assert result.auth_session.expires_at - result.auth_session.created_at <= timedelta(minutes=15)


def test_personal_account_cannot_use_workspace_routes(
    client: TestClient, created_users: list[int]
) -> None:
    _user_id, email = new_personal(created_users)
    assert login(client, email).status_code == status.HTTP_200_OK

    for path in ("/api/admin/sync-status", "/api/leagues/current"):
        response = client.get(path)
        assert response.status_code == status.HTTP_403_FORBIDDEN, path


# Navigation state -----------------------------------------------------------


def test_navigation_state_for_every_personal_state(
    registry: Registry, created_users: list[int]
) -> None:
    unverified = create_caller(registry, personal=True, verified=False)
    empty = create_caller(registry, personal=True)
    reverify = create_caller(registry, personal=True)
    revoked = create_caller(registry, personal=True)
    active = create_caller(registry, personal=True)
    season = seed_season(registry)
    other = seed_season(registry)
    grant(reverify.user_id, season.season_id, UserSeasonAccessStatus.REVERIFICATION_REQUIRED)
    grant(revoked.user_id, season.season_id, UserSeasonAccessStatus.REVOKED)
    grant(active.user_id, other.season_id)

    states = {
        name: get(caller, "/api/auth/me").json()
        for name, caller in {
            "unverified": unverified,
            "empty": empty,
            "reverify": reverify,
            "revoked": revoked,
            "active": active,
        }.items()
    }

    assert {name: me["navigation_state"] for name, me in states.items()} == {
        "unverified": "email_verification_required",
        "empty": "import_required",
        "reverify": "personal_dashboard",
        "revoked": "import_required",
        "active": "personal_dashboard",
    }
    for me in states.values():
        assert set(me) == ME_KEYS
        assert me["legacy_workspace"] is None


def test_deletion_recovery_session_reports_legacy_recovery_state(registry: Registry) -> None:
    from app.db.auth_models import AuthSessionPurpose as Purpose

    caller = create_caller(
        registry,
        registration_status=WorkspaceRegistrationStatus.DELETION_PENDING,
        purpose=Purpose.DELETION_RECOVERY,
    )

    me = get(caller, "/api/auth/me").json()

    assert me["navigation_state"] == "legacy_deletion_recovery"
    assert me["session_purpose"] == "deletion_recovery"


# Verification and recovery ------------------------------------------------------------------


def test_verification_without_a_workspace_resolves_only_the_intended_user(
    client: TestClient, created_users: list[int]
) -> None:
    first_email = f"verify-a-{uuid4().hex[:12]}@example.com"
    second_email = f"verify-b-{uuid4().hex[:12]}@example.com"

    for email in (first_email, second_email):
        assert (
            post(
                client,
                "/api/registration",
                {
                    "display_name": "Verifier",
                    "email": email,
                    "password": PASSWORD,
                    "confirm_password": PASSWORD,
                },
            ).status_code
            == status.HTTP_201_CREATED
        )

    with SessionFactory() as session:
        users = {
            email: session.scalar(select(User).where(User.normalized_email == email))
            for email in (first_email, second_email)
        }
        created_users.extend(user.id for user in users.values())
        first_delivery = identity_deliveries(session, users[first_email].id)[0]
        second_delivery = identity_deliveries(session, users[second_email].id)[0]

    message = deliver_identity_email(second_delivery.id)
    assert message.recipient == second_email

    token = deliver_and_extract_token(first_delivery.id)

    verified = post(client, "/api/registration/verify-email", {"token": token})

    assert verified.status_code == status.HTTP_200_OK
    assert set(verified.json()) == {
        "display_name",
        "email",
        "email_verified",
        "navigation_state",
        "csrf_token",
    }
    assert verified.json()["email"] == first_email
    assert verified.json()["navigation_state"] == "import_required"
    with SessionFactory() as session:
        assert session.get(User, users[first_email].id).email_verified_at is not None
        assert session.get(User, users[second_email].id).email_verified_at is None
    assert client.get("/api/auth/me").json()["navigation_state"] == "import_required"


def test_password_reset_for_a_personal_account_revokes_every_session_and_stays_workspace_free(
    client: TestClient, created_users: list[int]
) -> None:
    user_id, email = new_personal(created_users)
    browsers = [TestClient(app), TestClient(app)]

    for browser in browsers:
        with browser:
            assert login(browser, email).status_code == status.HTTP_200_OK
    assert active_sessions(user_id) == 2

    with SessionFactory() as session:
        user = session.get(User, user_id)
        token = issue_password_reset_token(session, user=user, lifetime_minutes=30).raw_token
        session.commit()

    with SessionFactory() as session:
        completed = reset_password(session, raw_token=token, new_password=NEW_PASSWORD)
        session.commit()

    assert completed.revoked_sessions == 2
    assert active_sessions(user_id) == 0
    with SessionFactory() as session:
        reasons = set(
            session.scalars(
                select(AuthSession.revocation_reason).where(AuthSession.user_id == user_id)
            )
        )
        notice = identity_deliveries(session, user_id, PASSWORD_CHANGED)
        assert reasons == {SessionRevocationReason.PASSWORD_RESET}
        assert [(d.scope, d.workspace_id) for d in notice] == [
            (TransactionalEmailScope.UNSCOPED, None)
        ]
    assert login(client, email).status_code == status.HTTP_401_UNAUTHORIZED
    assert login(client, email, NEW_PASSWORD).status_code == status.HTTP_200_OK


def test_password_reset_request_is_enumeration_safe_and_workspace_free(
    client: TestClient, created_users: list[int]
) -> None:
    user_id, email = new_personal(created_users)

    known = post(client, "/api/auth/password-reset/request", {"email": email})
    unknown = post(
        client, "/api/auth/password-reset/request", {"email": f"x-{uuid4().hex[:8]}@example.com"}
    )

    assert known.status_code == unknown.status_code == status.HTTP_202_ACCEPTED
    assert known.json() == unknown.json()
    with SessionFactory() as session:
        deliveries = identity_deliveries(session, user_id)
        assert [(d.scope, d.workspace_id) for d in deliveries] == [
            (TransactionalEmailScope.UNSCOPED, None)
        ]


@pytest.mark.parametrize("round_index", range(3))
def test_login_racing_a_password_reset_never_leaves_a_session_alive(
    created_users: list[int], round_index: int
) -> None:
    user_id, email = new_personal(created_users)

    with SessionFactory() as session:
        user = session.get(User, user_id)
        token = issue_password_reset_token(session, user=user, lifetime_minutes=30).raw_token
        session.commit()

    def worker(index: int) -> object:
        with SessionFactory() as session:
            if index == 0:
                reset_password(session, raw_token=token, new_password=NEW_PASSWORD)
            else:
                log_in_by_email(session, email=email, password=PASSWORD, session_lifetime_hours=1)
            session.commit()
            return "ok"

    results = run_concurrently(worker, 2)

    assert results[0] == "ok", results
    # Either order is valid, but the reset always wins: a login that checked the
    # old password either failed or its session was revoked by the reset.
    assert results[1] == "ok" or isinstance(results[1], InvalidCredentialsError), results
    assert active_sessions(user_id) == 0


# Existing import and workspace flows still work for a personal account ---------------------


def test_personal_account_can_read_a_season_it_has_access_to(registry: Registry) -> None:
    caller = create_caller(registry, personal=True)
    season = seed_season(registry)
    grant(caller.user_id, season.season_id)

    assert get(caller, "/api/leagues").status_code == status.HTTP_200_OK
