"""Routine API tests for ``POST /api/account/deletion`` (synthetic data only)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from app.auth.cookies import csrf_cookie_name
from app.core.settings import get_settings
from app.db.auth_models import AuthSession, AuthSessionPurpose, RegistrationRateLimit, User
from app.db.platform_enums import TransactionalEmailEventType
from app.db.platform_models import BackgroundJob, TransactionalEmailDelivery
from app.db.session import SessionFactory
from app.main import app
from tests.support.league_import import (  # noqa: F401
    TRUSTED_ORIGIN,
    Caller,
    create_caller,
    forbid_live_network,
    registry,
)

ENDPOINT = "/api/account/deletion"
PASSWORD = "correct horse battery staple"
PHRASE = "DELETE MY ACCOUNT"
ERROR_KEYS = {"detail", "code", "retryable"}


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def residue() -> Iterator[None]:
    """Remove the deliveries and jobs a test created (snapshot first)."""

    with SessionFactory() as session:
        keep_deliveries = set(session.scalars(select(TransactionalEmailDelivery.id)))
        keep_jobs = set(session.scalars(select(BackgroundJob.id)))

    yield

    with SessionFactory() as session:
        session.execute(delete(BackgroundJob).where(BackgroundJob.id.not_in(keep_jobs)))
        session.execute(
            delete(TransactionalEmailDelivery).where(
                TransactionalEmailDelivery.id.not_in(keep_deliveries)
            )
        )
        session.commit()


def _install_settings(*, keyring: bool) -> None:
    base = get_settings()
    update: dict[str, Any] = {"transactional_email_keys": None}

    if keyring:
        update = {
            "transactional_email_keys": SecretStr(f'{{"1": "{Fernet.generate_key().decode()}"}}'),
            "transactional_email_current_key_version": 1,
        }

    app.dependency_overrides[get_settings] = lambda: base.model_copy(update=update)


@pytest.fixture
def keyring_settings() -> Iterator[None]:
    _install_settings(keyring=True)

    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def no_keyring_settings() -> Iterator[None]:
    _install_settings(keyring=False)

    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


def deletion_body(
    password: str = PASSWORD, confirmation: str = PHRASE, **extra: Any
) -> dict[str, Any]:
    return {"current_password": password, "confirmation": confirmation, **extra}


def _client(caller: Caller | None, *, cookies: bool = True) -> TestClient:
    client = TestClient(app)

    if caller is not None and cookies:
        settings = get_settings()
        client.cookies.set(settings.session_cookie_name, caller.session_token)
        client.cookies.set(csrf_cookie_name(settings), caller.csrf_token)

    return client


def post_deletion(
    caller: Caller | None,
    body: dict[str, Any],
    *,
    origin: str | None = TRUSTED_ORIGIN,
    csrf: bool = True,
    cookies: bool = True,
):
    with _client(caller, cookies=cookies) as client:
        headers: dict[str, str] = {}

        if origin is not None:
            headers["Origin"] = origin

        if caller is not None and csrf:
            headers["X-CSRF-Token"] = caller.csrf_token

        return client.post(ENDPOINT, headers=headers, json=body)


def get_me(caller: Caller):
    with _client(caller) as client:
        return client.get("/api/auth/me")


def user_email(user_id: int) -> str:
    with SessionFactory() as session:
        email = session.scalar(select(User.normalized_email).where(User.id == user_id))

    assert email
    return email


def user_exists(user_id: int) -> bool:
    with SessionFactory() as session:
        return session.get(User, user_id) is not None


def session_exists(session_id: int) -> bool:
    with SessionFactory() as session:
        return session.get(AuthSession, session_id) is not None


def completion_deliveries() -> int:
    with SessionFactory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(TransactionalEmailDelivery)
                .where(
                    TransactionalEmailDelivery.event_type
                    == TransactionalEmailEventType.ACCOUNT_DELETION_COMPLETED
                )
            )
            or 0
        )


def assert_no_leak(text: str, caller: Caller, *others: Caller) -> None:
    assert PASSWORD not in text
    assert user_email(caller.user_id) not in text
    assert "password_hash" not in text
    assert "$argon2" not in text
    assert "$2b$" not in text

    for other in others:
        assert user_email(other.user_id) not in text


def assert_refusal(response, status_code: int, code: str, *, retryable: bool) -> None:
    assert response.status_code == status_code
    assert response.json() == {
        "detail": response.json()["detail"],
        "code": code,
        "retryable": retryable,
    }
    assert set(response.json()) == ERROR_KEYS


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


def test_success_deletes_account_and_clears_cookies(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    other = create_caller(registry, personal=True)
    email = user_email(caller.user_id)
    before = completion_deliveries()

    response = post_deletion(caller, deletion_body())

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "account_deleted"
    assert set(body) == {"detail", "code"}
    assert email not in response.text
    assert user_email(other.user_id) not in response.text
    assert PASSWORD not in response.text

    settings = get_settings()
    set_cookies = response.headers.get_list("set-cookie")
    for name in (settings.session_cookie_name, csrf_cookie_name(settings)):
        cleared = [value for value in set_cookies if value.startswith(f"{name}=")]
        assert cleared, name
        assert "max-age=0" in cleared[0].lower()

    assert not user_exists(caller.user_id)
    assert not session_exists(caller.session_id)
    assert user_exists(other.user_id)
    assert session_exists(other.session_id)
    assert get_me(caller).status_code == 401
    assert completion_deliveries() == before + 1


def test_success_creates_unscoped_completion_delivery(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    with SessionFactory() as session:
        known = set(session.scalars(select(TransactionalEmailDelivery.id)))

    assert post_deletion(caller, deletion_body()).status_code == 200

    with SessionFactory() as session:
        created = list(
            session.scalars(
                select(TransactionalEmailDelivery).where(
                    TransactionalEmailDelivery.id.not_in(known)
                )
            )
        )

    assert len(created) == 1
    assert created[0].event_type is TransactionalEmailEventType.ACCOUNT_DELETION_COMPLETED
    assert created[0].user_id is None


@pytest.mark.parametrize(
    "confirmation",
    ["delete my account", "  Delete   My\tAccount  ", "DELETE MY ACCOUNT"],
)
def test_confirmation_phrase_is_case_and_whitespace_tolerant(
    registry,  # noqa: F811
    keyring_settings,
    confirmation,
) -> None:
    caller = create_caller(registry, personal=True)

    response = post_deletion(caller, deletion_body(confirmation=confirmation))

    assert response.status_code == 200
    assert not user_exists(caller.user_id)


# --------------------------------------------------------------------------
# Refusals that keep the account
# --------------------------------------------------------------------------


def test_wrong_password_is_retryable_and_deletes_nothing(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    other = create_caller(registry, personal=True)
    before = completion_deliveries()

    response = post_deletion(caller, deletion_body(password="wrong synthetic password"))

    assert_refusal(response, 400, "invalid_current_password", retryable=True)
    assert "wrong synthetic password" not in response.text
    assert_no_leak(response.text, caller, other)
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)
    assert completion_deliveries() == before
    assert get_me(caller).status_code == 200


def test_wrong_confirmation_is_retryable_and_deletes_nothing(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    other = create_caller(registry, personal=True)
    before = completion_deliveries()

    response = post_deletion(caller, deletion_body(confirmation="DELETE ACCOUNT"))

    assert_refusal(response, 400, "account_deletion_confirmation_mismatch", retryable=True)
    assert_no_leak(response.text, caller, other)
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)
    assert completion_deliveries() == before


def test_wrong_password_takes_precedence_over_wrong_confirmation(
    registry,  # noqa: F811
    keyring_settings,
) -> None:
    caller = create_caller(registry, personal=True)

    response = post_deletion(caller, deletion_body(password="wrong synthetic", confirmation="no"))

    assert_refusal(response, 400, "invalid_current_password", retryable=True)
    assert user_exists(caller.user_id)


@pytest.mark.parametrize("state", ["legacy_admin", "unverified_personal"])
def test_unavailable_states_get_one_non_retryable_refusal(
    registry,  # noqa: F811
    keyring_settings,
    state,
) -> None:
    caller = (
        create_caller(registry)
        if state == "legacy_admin"
        else create_caller(registry, personal=True, verified=False)
    )
    before = completion_deliveries()

    response = post_deletion(caller, deletion_body())

    assert_refusal(response, 409, "account_deletion_unavailable", retryable=False)
    assert_no_leak(response.text, caller)
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)
    assert completion_deliveries() == before


def test_unavailable_state_does_not_reveal_password_correctness(
    registry,  # noqa: F811
    keyring_settings,
) -> None:
    caller = create_caller(registry, personal=True, verified=False)

    right = post_deletion(caller, deletion_body())
    wrong = post_deletion(caller, deletion_body(password="wrong synthetic password"))

    assert right.status_code == wrong.status_code == 409
    assert right.json() == wrong.json()


def test_missing_transactional_email_keyring_refuses_and_keeps_account(
    registry,  # noqa: F811
    no_keyring_settings,
) -> None:
    caller = create_caller(registry, personal=True)
    other = create_caller(registry, personal=True)
    before = completion_deliveries()

    response = post_deletion(caller, deletion_body())

    assert_refusal(response, 503, "account_deletion_notice_unavailable", retryable=True)
    assert_no_leak(response.text, caller, other)
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)
    assert completion_deliveries() == before
    assert get_me(caller).status_code == 200


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"current_password": PASSWORD},
        {"confirmation": PHRASE},
        {"current_password": "", "confirmation": PHRASE},
        {"current_password": PASSWORD, "confirmation": ""},
        {"current_password": "x" * 129, "confirmation": PHRASE},
        {"current_password": PASSWORD, "confirmation": "x" * 65},
        {"current_password": 123, "confirmation": PHRASE},
        {"current_password": PASSWORD, "confirmation": None},
    ],
    ids=[
        "empty",
        "no-confirmation",
        "no-password",
        "blank-password",
        "blank-confirmation",
        "password-over-128",
        "confirmation-over-64",
        "password-not-string",
        "confirmation-null",
    ],
)
def test_invalid_bodies_are_rejected_without_deleting(registry, keyring_settings, body) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    response = post_deletion(caller, body)

    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)


def test_boundary_lengths_are_accepted_by_validation(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    response = post_deletion(caller, deletion_body(password="x" * 128, confirmation="y" * 64))

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_current_password"
    assert user_exists(caller.user_id)


# --------------------------------------------------------------------------
# Authentication, CSRF, origin, method
# --------------------------------------------------------------------------


def test_missing_session_cookie_is_unauthorized(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    response = post_deletion(caller, deletion_body(), cookies=False)

    assert response.status_code == 401
    assert user_exists(caller.user_id)


def test_unauthenticated_request_is_unauthorized(keyring_settings) -> None:
    response = post_deletion(None, deletion_body())

    assert response.status_code == 401


def test_missing_csrf_header_is_forbidden(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    response = post_deletion(caller, deletion_body(), csrf=False)

    assert response.status_code == 403
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)


def test_incorrect_csrf_header_is_forbidden(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    with _client(caller) as client:
        response = client.post(
            ENDPOINT,
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": "synthetic-wrong-csrf"},
            json=deletion_body(),
        )

    assert response.status_code == 403
    assert user_exists(caller.user_id)


def test_untrusted_origin_is_forbidden(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    response = post_deletion(caller, deletion_body(), origin="https://evil.example.invalid")

    assert response.status_code == 403
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)


def test_missing_origin_follows_the_shared_origin_policy(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    settings = get_settings()

    response = post_deletion(caller, deletion_body(password="wrong synthetic"), origin=None)

    if settings.is_production:
        assert response.status_code == 403
    else:
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_current_password"

    assert user_exists(caller.user_id)


def test_deletion_recovery_session_is_refused(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True, purpose=AuthSessionPurpose.DELETION_RECOVERY)

    response = post_deletion(caller, deletion_body())

    assert response.status_code == 403
    assert response.json()["code"] == "session_restricted"
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)


def test_get_is_not_allowed(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    with _client(caller) as client:
        response = client.get(ENDPOINT, headers={"Origin": TRUSTED_ORIGIN})

    assert response.status_code == 405
    assert user_exists(caller.user_id)


# --------------------------------------------------------------------------
# Throttle
# --------------------------------------------------------------------------


def test_sixth_attempt_is_throttled_with_retry_after(registry, keyring_settings) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    other = create_caller(registry, personal=True)

    for _ in range(5):
        response = post_deletion(caller, deletion_body(password="wrong synthetic password"))
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_current_password"

    throttled = post_deletion(caller, deletion_body(password="wrong synthetic password"))

    assert_refusal(throttled, 429, "account_deletion_throttled", retryable=True)
    assert int(throttled.headers["Retry-After"]) > 0
    assert_no_leak(throttled.text, caller, other)

    # The throttle precedes the password check: even the correct password is refused.
    correct = post_deletion(caller, deletion_body())

    assert correct.status_code == 429
    assert user_exists(caller.user_id)
    assert session_exists(caller.session_id)


def test_throttle_attempts_are_persisted_before_the_password_check(
    registry,  # noqa: F811
    keyring_settings,
) -> None:
    caller = create_caller(registry, personal=True)

    with SessionFactory() as session:
        before = int(session.scalar(select(func.count()).select_from(RegistrationRateLimit)) or 0)

    assert post_deletion(caller, deletion_body(password="wrong synthetic")).status_code == 400

    with SessionFactory() as session:
        after = int(session.scalar(select(func.count()).select_from(RegistrationRateLimit)) or 0)

    assert after > before
