"""Critical durability, concurrency, and secrecy tests for identity email.

Contract (ADR 0009, durable identity-email slice):

- Public identity routes write business state, one delivery intent, and its
  BackgroundJob in one transaction; they never create a token or call a
  provider.
- Only the transactional-email worker creates a token hash, linked to the
  delivery; older tokens are superseded only after a newer delivery is
  accepted.
- Concurrency tests use real PostgreSQL connections (one SessionFactory
  session per thread) released together by a barrier. The authoritative
  outcome is the committed row set. Mocks never replace PostgreSQL locking.

All addresses, tokens, and provider responses are synthetic.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.auth.security import verify_password
from app.core.settings import get_settings
from app.db.auth_models import (
    AuthSession,
    EmailVerificationToken,
    PasswordResetToken,
    RegistrationRateLimit,
    User,
    UserRole,
)
from app.db.platform_enums import (
    BackgroundJobStatus,
    InstallationStatus,
    OnboardingStage,
    RegistrationMode,
    TransactionalEmailDeliveryStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation, TransactionalEmailDelivery
from app.db.session import SessionFactory, engine
from app.main import app
from app.repositories.account_token_repository import AccountTokenRepository
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.transactional_email_delivery_repository import (
    TransactionalEmailDeliveryRepository,
)
from app.services.administrator_registration import (
    DuplicateAdministratorEmailError,
    register_provisional_administrator,
)
from app.services.identity_email import (
    password_changed_key,
    password_reset_key,
    queue_password_reset,
    queue_verification_resend,
    registration_verification_key,
    verification_resend_key,
)
from app.services.password_recovery import reset_administrator_password
from app.services.transactional_email_intents import queue_workspace_email
from tests.support.identity_email import (
    PASSWORD_CHANGED,
    PASSWORD_RESET,
    VERIFICATION,
    deliver_and_extract_token,
    email_jobs_for,
    forbid_provider_and_network,
    identity_deliveries,
    issue_email_verification_token,
    issue_password_reset_token,
    token_count,
)

TRUSTED_ORIGIN = "http://localhost:5173"
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "an entirely new synthetic passphrase"
THREADS = 6


# Fixtures -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_provider_or_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return forbid_provider_and_network(monkeypatch)


@pytest.fixture(autouse=True)
def isolate_rate_limit_rows() -> Iterator[None]:
    with SessionFactory() as session:
        existing = set(session.scalars(select(RegistrationRateLimit.id)).all())

    yield

    with SessionFactory() as session:
        cleanup = delete(RegistrationRateLimit)

        if existing:
            cleanup = cleanup.where(RegistrationRateLimit.id.not_in(existing))

        session.execute(cleanup)
        session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"registration_mode": RegistrationMode.OPEN}
    )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    app.dependency_overrides.pop(get_settings, None)


def csrf(client: TestClient) -> str:
    response = client.get("/api/auth/csrf")
    assert response.status_code == status.HTTP_200_OK
    return response.json()["csrf_token"]


def post(client: TestClient, path: str, body: dict[str, object]):
    return client.post(
        path,
        headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf(client)},
        json=body,
    )


def registration_body() -> dict[str, str]:
    suffix = uuid4().hex[:12]
    return {
        "workspace_name": "Durable Identity League",
        "workspace_slug": f"durable-{suffix}",
        "display_name": "Durable Administrator",
        "email": f"durable-{suffix}@example.com",
        "password": PASSWORD,
    }


def delete_workspaces(workspace_ids: list[int]) -> None:
    with SessionFactory() as session:
        for workspace_id in workspace_ids:
            for user in session.scalars(select(User).where(User.workspace_id == workspace_id)):
                session.delete(user)

            session.flush()
            workspace = session.get(Installation, workspace_id)

            if workspace is not None:
                session.delete(workspace)

        session.commit()


def delete_by_email(email: str) -> None:
    with SessionFactory() as session:
        ids = list(
            session.scalars(select(User.workspace_id).where(User.normalized_email == email)).all()
        )

    delete_workspaces([workspace_id for workspace_id in ids if workspace_id is not None])


def create_administrator(*, provisional: bool) -> tuple[int, int, str]:
    """Commit a workspace and administrator; return (workspace, user, email)."""

    suffix = uuid4().hex[:12]
    email = f"identity-{suffix}@example.com"

    with SessionFactory() as session:
        workspace = Installation(
            application_name="Identity Email League",
            slug=f"identity-{suffix}",
            normalized_slug=f"identity-{suffix}",
            timezone="America/Chicago",
            status=(
                InstallationStatus.SETUP_IN_PROGRESS if provisional else InstallationStatus.ACTIVE
            ),
            registration_status=(
                WorkspaceRegistrationStatus.PROVISIONAL
                if provisional
                else WorkspaceRegistrationStatus.ACTIVE
            ),
            onboarding_stage=(
                OnboardingStage.EMAIL_VERIFICATION_PENDING
                if provisional
                else OnboardingStage.COMPLETE
            ),
        )
        session.add(workspace)
        session.flush()
        administrator = AuthenticationRepository(session).create_user(
            username=f"internal-admin-{suffix}",
            display_name="Identity Administrator",
            email=email,
            password=PASSWORD,
            role=UserRole.ADMIN,
            workspace_id=workspace.id,
        )

        if not provisional:
            administrator.email_verified_at = datetime.now(UTC)

        session.commit()
        return workspace.id, administrator.id, email


@pytest.fixture
def provisional_admin() -> Iterator[tuple[int, int, str]]:
    created = create_administrator(provisional=True)
    yield created
    delete_workspaces([created[0]])


@pytest.fixture
def active_admin() -> Iterator[tuple[int, int, str]]:
    created = create_administrator(provisional=False)
    yield created
    delete_workspaces([created[0]])


def run_concurrently(worker: Callable[[int], object], count: int = THREADS) -> list[object]:
    """Release every worker at once; each opens its own connection."""

    barrier = threading.Barrier(count)

    def synchronized(index: int) -> object:
        barrier.wait(timeout=10)

        try:
            return worker(index)
        except Exception as exc:  # noqa: BLE001 - results are asserted
            return exc

    with ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(synchronized, range(count)))


def max_id(model) -> int:
    with SessionFactory() as session:
        return int(session.scalar(select(func.coalesce(func.max(model.id), 0))) or 0)


def mark_delivery_sent(delivery_id: int) -> None:
    with SessionFactory() as session:
        session.execute(
            update(TransactionalEmailDelivery)
            .where(TransactionalEmailDelivery.id == delivery_id)
            .values(
                status=TransactionalEmailDeliveryStatus.SENT,
                sent_at=datetime.now(UTC),
                provider_message_id="synthetic-sent",
                provider_message_id_expires_at=datetime.now(UTC) + timedelta(days=30),
                retention_expires_at=datetime.now(UTC) + timedelta(days=90),
            )
        )
        session.commit()


def active_jobs(session, delivery_ids: list[int]) -> list[BackgroundJob]:
    return [
        job
        for job in email_jobs_for(session, delivery_ids)
        if job.status in (BackgroundJobStatus.PENDING, BackgroundJobStatus.RETRY_SCHEDULED)
    ]


# Registration ---------------------------------------------------------------


def test_registration_atomically_queues_verification_without_token(
    client: TestClient, no_provider_or_network: list[str]
) -> None:
    body = registration_body()

    try:
        response = post(client, "/api/registration", body)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {
            "workspace_slug": body["workspace_slug"],
            "registration_status": "provisional",
            "onboarding_stage": "email_verification_pending",
            "verification_required": True,
        }

        with SessionFactory() as session:
            user = session.scalar(select(User).where(User.normalized_email == body["email"]))
            assert user is not None and user.workspace_id is not None
            deliveries = identity_deliveries(session, user.id)
            jobs = email_jobs_for(session, [delivery.id for delivery in deliveries])

            assert len(deliveries) == 1
            delivery = deliveries[0]
            assert delivery.event_type is VERIFICATION
            assert delivery.workspace_id == user.workspace_id
            assert delivery.idempotency_key == registration_verification_key(user.id)
            assert delivery.status is TransactionalEmailDeliveryStatus.PENDING
            assert len(jobs) == 1
            assert jobs[0].workspace_id == user.workspace_id
            assert jobs[0].payload == {"version": 1, "delivery_id": delivery.id}
            # Token creation belongs to the worker only.
            assert token_count(session, user.id) == 0

        assert no_provider_or_network == []

        raw_token = deliver_and_extract_token(delivery.id)

        with SessionFactory() as session:
            tokens = list(
                session.scalars(
                    select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id)
                )
            )
            assert len(tokens) == 1
            assert tokens[0].transactional_email_delivery_id == delivery.id
            assert raw_token not in tokens[0].token_hash

        verified = post(client, "/api/registration/verify-email", {"token": raw_token})
        assert verified.status_code == status.HTTP_200_OK
    finally:
        delete_by_email(body["email"])


@pytest.mark.parametrize("failure_point", ["delivery", "job"])
def test_registration_rolls_back_everything_when_intent_creation_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_point: str,
) -> None:
    body = registration_body()
    before = {
        model: max_id(model)
        for model in (
            Installation,
            User,
            TransactionalEmailDelivery,
            BackgroundJob,
            EmailVerificationToken,
        )
    }

    def fail(*_args: object, **_kwargs: object):
        raise SQLAlchemyError("synthetic local intent failure")

    if failure_point == "delivery":
        monkeypatch.setattr(TransactionalEmailDeliveryRepository, "create_workspace_delivery", fail)
    else:
        # The delivery row is flushed first; the job write then fails.
        monkeypatch.setattr(BackgroundJobRepository, "enqueue", fail)

    try:
        with caplog.at_level(logging.INFO):
            response = post(client, "/api/registration", body)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # Test settings enable debug tracebacks; production maps this through
        # the generic unexpected-error handler. Never a provider contract.
        assert "email_delivery_unavailable" not in response.text
        assert "email_delivery_unavailable" not in response.text
        assert body["email"] not in caplog.text

        with SessionFactory() as session:
            assert (
                session.scalar(
                    select(Installation).where(
                        Installation.normalized_slug == body["workspace_slug"]
                    )
                )
                is None
            )
            assert (
                session.scalar(select(User).where(User.normalized_email == body["email"])) is None
            )

        for model, previous in before.items():
            with SessionFactory() as session:
                newer = session.scalar(
                    select(func.count()).select_from(model).where(model.id > previous)
                )
            assert newer == 0, model.__name__
    finally:
        delete_by_email(body["email"])


def test_duplicate_email_registration_creates_nothing_and_matches_response(
    client: TestClient,
) -> None:
    first = registration_body()
    second = registration_body()
    second["email"] = first["email"]

    try:
        created = post(client, "/api/registration", first)
        assert created.status_code == status.HTTP_201_CREATED

        with SessionFactory() as session:
            user = session.scalar(select(User).where(User.normalized_email == first["email"]))
            assert user is not None
            user_id = user.id

        before = (max_id(Installation), max_id(User), max_id(TransactionalEmailDelivery))
        before_jobs = max_id(BackgroundJob)

        duplicate = post(client, "/api/registration", second)

        assert duplicate.status_code == created.status_code
        assert duplicate.json() == {**created.json(), "workspace_slug": second["workspace_slug"]}
        assert (max_id(Installation), max_id(User), max_id(TransactionalEmailDelivery)) == before
        assert max_id(BackgroundJob) == before_jobs

        with SessionFactory() as session:
            assert len(identity_deliveries(session, user_id)) == 1
            assert token_count(session, user_id) == 0
    finally:
        delete_by_email(first["email"])


def test_concurrent_same_email_registration_queues_exactly_one_delivery() -> None:
    email = f"concurrent-durable-{uuid4().hex[:12]}@example.com"
    slugs = [f"concurrent-durable-{uuid4().hex[:12]}" for _ in range(THREADS)]

    def register(index: int) -> object:
        with SessionFactory() as session:
            result = register_provisional_administrator(
                session,
                registration_mode=RegistrationMode.OPEN,
                workspace_name="Concurrent Durable League",
                workspace_slug=slugs[index],
                administrator_display_name="Concurrent Administrator",
                administrator_email=email,
                administrator_password=PASSWORD,
            )
            session.commit()
            return result.administrator.id

    try:
        results = run_concurrently(register)
        winners = [result for result in results if isinstance(result, int)]
        losers = [r for r in results if isinstance(r, DuplicateAdministratorEmailError)]

        assert len(winners) == 1, results
        assert len(losers) == THREADS - 1, results

        with SessionFactory() as session:
            workspaces = session.scalars(
                select(Installation).where(Installation.normalized_slug.in_(slugs))
            ).all()
            deliveries = identity_deliveries(session, winners[0])

            assert len(workspaces) == 1
            assert len(deliveries) == 1
            assert deliveries[0].workspace_id == workspaces[0].id
            assert len(email_jobs_for(session, [deliveries[0].id])) == 1
            assert token_count(session, winners[0]) == 0
    finally:
        delete_by_email(email)


# Resend and reset issuance concurrency ------------------------------------


@pytest.mark.parametrize(
    ("event_type", "queue", "key_for", "fixture_name"),
    [
        (VERIFICATION, queue_verification_resend, verification_resend_key, "provisional_admin"),
        (PASSWORD_RESET, queue_password_reset, password_reset_key, "active_admin"),
    ],
)
def test_concurrent_issuance_is_bounded_then_allows_deliberate_reissue(
    request: pytest.FixtureRequest,
    event_type,
    queue,
    key_for,
    fixture_name: str,
) -> None:
    """Locks: the service holds the administrator row FOR UPDATE; the unique
    delivery idempotency key is the backstop. Forbidden: shared sessions,
    mocked locking, or asserting on in-memory results instead of rows."""

    workspace_id, user_id, _email = request.getfixturevalue(fixture_name)

    def issue(_index: int) -> object:
        with SessionFactory() as session:
            administrator = session.get(User, user_id)
            queued = queue(session, administrator=administrator)
            session.commit()
            return queued.delivery.id

    results = run_concurrently(issue)
    assert all(isinstance(result, int) for result in results), results
    assert len(set(results)) == 1

    with SessionFactory() as session:
        deliveries = identity_deliveries(session, user_id, event_type)
        assert [delivery.id for delivery in deliveries] == [results[0]]
        assert deliveries[0].workspace_id == workspace_id
        assert deliveries[0].idempotency_key == key_for(user_id, 0)
        assert len(active_jobs(session, [results[0]])) == 1
        assert token_count(session, user_id) == 0

    first_id = results[0]
    mark_delivery_sent(first_id)

    second = run_concurrently(issue)
    assert all(isinstance(result, int) for result in second), second
    assert len(set(second)) == 1 and second[0] != first_id

    with SessionFactory() as session:
        deliveries = identity_deliveries(session, user_id, event_type)
        assert [delivery.id for delivery in deliveries] == [first_id, second[0]]
        assert deliveries[1].idempotency_key == key_for(user_id, first_id)
        assert len(active_jobs(session, [second[0]])) == 1
        assert token_count(session, user_id) == 0


def test_identical_delivery_key_on_separate_connections_creates_one_delivery_and_job(
    active_admin: tuple[int, int, str],
) -> None:
    """No user lock here: only the unique idempotency indexes serialize."""

    workspace_id, user_id, _email = active_admin
    key = password_reset_key(user_id, 0)

    def create(_index: int) -> object:
        with SessionFactory() as session:
            queued = queue_workspace_email(
                session,
                workspace_id=workspace_id,
                user_id=user_id,
                event_type=PASSWORD_RESET,
                idempotency_key=key,
            )
            session.commit()
            return (queued.delivery.id, queued.job.id if queued.job else None)

    results = run_concurrently(create)
    assert all(isinstance(result, tuple) for result in results), results
    assert len(set(results)) == 1

    with SessionFactory() as session:
        deliveries = identity_deliveries(session, user_id, PASSWORD_RESET)
        assert len(deliveries) == 1
        assert len(email_jobs_for(session, [deliveries[0].id])) == 1


# Token boundary and acceptance-based supersession ---------------------------


@pytest.mark.parametrize(
    ("path", "fixture_name", "event_type", "token_model", "issue_legacy"),
    [
        (
            "/api/registration/resend-verification",
            "provisional_admin",
            VERIFICATION,
            EmailVerificationToken,
            lambda session, user: issue_email_verification_token(
                session, user=user, lifetime_hours=24
            ),
        ),
        (
            "/api/auth/password-reset/request",
            "active_admin",
            PASSWORD_RESET,
            PasswordResetToken,
            lambda session, user: issue_password_reset_token(
                session, user=user, lifetime_minutes=30
            ),
        ),
    ],
)
def test_route_creates_no_token_and_older_token_survives_until_acceptance(
    request: pytest.FixtureRequest,
    client: TestClient,
    no_provider_or_network: list[str],
    path: str,
    fixture_name: str,
    event_type,
    token_model,
    issue_legacy,
) -> None:
    _workspace_id, user_id, email = request.getfixturevalue(fixture_name)

    with SessionFactory() as session:
        legacy = issue_legacy(session, session.get(User, user_id))
        session.commit()

    response = post(client, path, {"email": email})
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert no_provider_or_network == []

    with SessionFactory() as session:
        tokens = list(session.scalars(select(token_model).where(token_model.user_id == user_id)))
        # The route created nothing and invalidated nothing.
        assert len(tokens) == 1 and tokens[0].used_at is None
        deliveries = identity_deliveries(session, user_id, event_type)
        assert len(deliveries) == 1

    deliver_and_extract_token(deliveries[0].id)

    with SessionFactory() as session:
        tokens = list(
            session.scalars(
                select(token_model).where(token_model.user_id == user_id).order_by(token_model.id)
            )
        )
        assert len(tokens) == 2
        assert tokens[0].used_at is not None, "accepted delivery supersedes the legacy token"
        assert tokens[1].transactional_email_delivery_id == deliveries[0].id
        assert tokens[1].used_at is None
        assert legacy.raw_token not in tokens[1].token_hash


# Enumeration-sensitive local work ------------------------------------------


@pytest.mark.parametrize(
    ("path", "fixture_name"),
    [
        ("/api/registration/resend-verification", "provisional_admin"),
        ("/api/auth/password-reset/request", "active_admin"),
    ],
)
def test_eligible_and_unknown_requests_are_local_bounded_and_identical(
    request: pytest.FixtureRequest,
    client: TestClient,
    no_provider_or_network: list[str],
    path: str,
    fixture_name: str,
) -> None:
    _workspace_id, user_id, email = request.getfixturevalue(fixture_name)
    unknown = f"unknown-{uuid4().hex[:12]}@example.com"
    statements: list[str] = []

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    responses = {}
    counts = {}

    for label, address in (("eligible", email), ("unknown", unknown)):
        csrf_token = csrf(client)
        statements.clear()
        event.listen(engine, "before_cursor_execute", count)

        try:
            responses[label] = client.post(
                path,
                headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token},
                json={"email": address},
            )
        finally:
            event.remove(engine, "before_cursor_execute", count)

        counts[label] = len(statements)

    assert responses["eligible"].status_code == responses["unknown"].status_code
    assert responses["eligible"].json() == responses["unknown"].json()
    assert no_provider_or_network == []
    # Both branches do a small, bounded amount of local database work.
    assert counts["eligible"] <= 30 and counts["unknown"] <= 30, counts

    with SessionFactory() as session:
        assert token_count(session, user_id) == 0


# Secrecy and ownership ----------------------------------------------------


def test_public_responses_logs_and_queue_rows_carry_no_secrets(
    client: TestClient,
    active_admin: tuple[int, int, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_id, user_id, email = active_admin

    with caplog.at_level(logging.DEBUG):
        response = post(client, "/api/auth/password-reset/request", {"email": email})

        with SessionFactory() as session:
            delivery = identity_deliveries(session, user_id, PASSWORD_RESET)[0]
            job = email_jobs_for(session, [delivery.id])[0]
            delivery_id, job_id = delivery.id, job.id
            assert job.payload == {"version": 1, "delivery_id": delivery.id}
            assert delivery.encrypted_recipient is None
            assert "@" not in delivery.idempotency_key
            assert email.split("@")[0] not in delivery.idempotency_key

        raw_token = deliver_and_extract_token(delivery_id)

    text = response.text
    assert email not in text
    assert f'"{delivery_id}"' not in text and str(job_id) not in text
    for forbidden in ("delivery", "job", "provider", "token", "have been sent", "delivered"):
        assert forbidden not in text.lower()
    assert raw_token not in caplog.text
    assert email not in caplog.text

    with SessionFactory() as session:
        stored = session.get(TransactionalEmailDelivery, delivery_id)
        assert stored.workspace_id == workspace_id
        assert raw_token not in (stored.idempotency_key or "")
        assert raw_token not in str(session.get(BackgroundJob, job_id).payload)


def test_request_body_cannot_select_workspace_or_user(
    client: TestClient,
    active_admin: tuple[int, int, str],
) -> None:
    workspace_id, user_id, email = active_admin
    other_workspace, other_user, _ = create_administrator(provisional=False)

    try:
        response = post(
            client,
            "/api/auth/password-reset/request",
            {"email": email, "workspace_id": other_workspace, "user_id": other_user},
        )
        assert response.status_code in (status.HTTP_202_ACCEPTED, 422)

        with SessionFactory() as session:
            assert identity_deliveries(session, other_user) == []
            for delivery in identity_deliveries(session, user_id):
                assert delivery.workspace_id == workspace_id
    finally:
        delete_workspaces([other_workspace])


# Password-reset completion ------------------------------------------------


def _open_session(user_id: int) -> int:
    with SessionFactory() as session:
        created = SessionRepository(session).replace_user_sessions(
            user=session.get(User, user_id), lifetime_hours=1
        )
        session.commit()
        return created.session.id


def test_completion_changes_password_and_queues_tokenless_notice_atomically(
    client: TestClient,
    active_admin: tuple[int, int, str],
) -> None:
    _workspace_id, user_id, _email = active_admin

    with SessionFactory() as session:
        user = session.get(User, user_id)
        legacy = issue_password_reset_token(session, user=user, lifetime_minutes=30)
        consumed_id = session.scalar(
            select(PasswordResetToken.id).where(PasswordResetToken.user_id == user_id)
        )
        queued = queue_password_reset(session, administrator=user)
        AccountTokenRepository(session).create_delivery_password_reset_token(
            user_id=user_id,
            raw_token=f"synthetic-other-{uuid4().hex}",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            delivery_id=queued.delivery.id,
            delivery_attempt=1,
        )
        session.commit()

    auth_session_id = _open_session(user_id)
    tokens_before = max_id(PasswordResetToken)

    response = post(
        client,
        "/api/auth/password-reset/complete",
        {"token": legacy.raw_token, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == status.HTTP_200_OK

    with SessionFactory() as session:
        user = session.get(User, user_id)
        assert verify_password(NEW_PASSWORD, user.password_hash)
        remaining = session.scalars(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
        ).all()
        assert all(token.used_at is not None for token in remaining)
        assert session.get(AuthSession, auth_session_id).revoked_at is not None
        notices = identity_deliveries(session, user_id, PASSWORD_CHANGED)
        assert len(notices) == 1
        assert notices[0].idempotency_key == password_changed_key(user_id, consumed_id)
        assert len(email_jobs_for(session, [notices[0].id])) == 1

    assert max_id(PasswordResetToken) == tokens_before

    replay = post(
        client,
        "/api/auth/password-reset/complete",
        {"token": legacy.raw_token, "new_password": NEW_PASSWORD + " again"},
    )
    assert replay.status_code == status.HTTP_400_BAD_REQUEST

    with SessionFactory() as session:
        assert len(identity_deliveries(session, user_id, PASSWORD_CHANGED)) == 1


def test_completion_rolls_back_when_notice_cannot_be_queued(
    client: TestClient,
    active_admin: tuple[int, int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace_id, user_id, _email = active_admin

    with SessionFactory() as session:
        legacy = issue_password_reset_token(
            session, user=session.get(User, user_id), lifetime_minutes=30
        )
        session.commit()

    auth_session_id = _open_session(user_id)

    def fail(*_args: object, **_kwargs: object):
        raise SQLAlchemyError("synthetic local intent failure")

    monkeypatch.setattr(BackgroundJobRepository, "enqueue", fail)

    response = post(
        client,
        "/api/auth/password-reset/complete",
        {"token": legacy.raw_token, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "email_delivery_unavailable" not in response.text

    with SessionFactory() as session:
        user = session.get(User, user_id)
        assert verify_password(PASSWORD, user.password_hash)
        token = session.scalar(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
        )
        assert token.used_at is None
        assert session.get(AuthSession, auth_session_id).revoked_at is None
        assert identity_deliveries(session, user_id) == []


def test_invalid_completion_changes_nothing_and_queues_nothing(
    client: TestClient,
    active_admin: tuple[int, int, str],
) -> None:
    _workspace_id, user_id, _email = active_admin

    response = post(
        client,
        "/api/auth/password-reset/complete",
        {"token": f"synthetic-unknown-{uuid4().hex}", "new_password": NEW_PASSWORD},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST

    with SessionFactory() as session:
        assert verify_password(PASSWORD, session.get(User, user_id).password_hash)
        assert identity_deliveries(session, user_id) == []


def test_concurrent_completion_with_one_token_queues_one_notice(
    active_admin: tuple[int, int, str],
) -> None:
    _workspace_id, user_id, _email = active_admin

    with SessionFactory() as session:
        legacy = issue_password_reset_token(
            session, user=session.get(User, user_id), lifetime_minutes=30
        )
        session.commit()

    def complete(index: int) -> object:
        with SessionFactory() as session:
            reset_administrator_password(
                session, raw_token=legacy.raw_token, new_password=f"{NEW_PASSWORD} {index}"
            )
            session.commit()
            return index

    results = run_concurrently(complete)
    assert len([result for result in results if isinstance(result, int)]) == 1, results

    with SessionFactory() as session:
        notices = identity_deliveries(session, user_id, PASSWORD_CHANGED)
        assert len(notices) == 1
        assert len(email_jobs_for(session, [notices[0].id])) == 1
