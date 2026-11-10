from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth.security import hmac_identifier
from app.core.settings import get_settings
from app.db.auth_models import (
    LoginAttempt,
    UserRole,
)
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import (
    AuthenticationRepository,
)

TRUSTED_ORIGIN = "http://localhost:5173"


def clear_username_throttle_rows(
    username: str,
) -> None:
    settings = get_settings()
    secret = settings.app_secret_key.get_secret_value()

    username_hashes = {
        hmac_identifier(
            username,
            secret=secret,
            namespace=namespace,
        )
        for namespace in (
            "login-username",
            "client-login-username",
            "admin-login-account",
        )
    }

    with SessionFactory() as session:
        session.execute(delete(LoginAttempt).where(LoginAttempt.username_hash.in_(username_hashes)))
        session.commit()


def clear_testclient_throttle_rows() -> None:
    settings = get_settings()
    source_hash = hmac_identifier(
        "testclient",
        secret=settings.app_secret_key.get_secret_value(),
        namespace="login-source",
    )

    with SessionFactory() as session:
        session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.source_hash == source_hash,
            )
        )
        session.commit()


@pytest.fixture(autouse=True)
def isolate_login_throttle_rows() -> Iterator[None]:
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
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def login_credentials() -> tuple[str, str]:
    unique = uuid4().hex[:12]
    username = f"http-user-{unique}"
    password = "correct horse battery staple"

    with SessionFactory() as session:
        repository = AuthenticationRepository(session)

        repository.create_user(
            username=username,
            display_name="HTTP Test User",
            password=password,
        )

        session.commit()

    yield username, password

    clear_username_throttle_rows(username)

    with SessionFactory() as session:
        user = AuthenticationRepository(session).get_user_by_username(username)

        if user is not None:
            session.delete(user)

        session.commit()


@pytest.fixture
def admin_login_credentials() -> tuple[str, str]:
    unique = uuid4().hex[:12]
    username = f"http-admin-{unique}"
    password = "correct horse battery staple"

    with SessionFactory() as session:
        repository = AuthenticationRepository(session)

        repository.create_user(
            username=username,
            display_name="HTTP Test Administrator",
            password=password,
            role=UserRole.ADMIN,
        )

        session.commit()

    yield username, password

    clear_username_throttle_rows(username)

    with SessionFactory() as session:
        user = AuthenticationRepository(session).get_user_by_username(username)

        if user is not None:
            session.delete(user)

        session.commit()


def issue_csrf(
    client: TestClient,
) -> str:
    response = client.get("/api/auth/csrf")

    assert response.status_code == status.HTTP_200_OK

    token = response.json()["csrf_token"]

    assert token
    assert "fantasy_preauth_csrf" in client.cookies

    return token


def login(
    client: TestClient,
    *,
    username: str,
    password: str,
) -> dict:
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/auth/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "username": username,
            "password": password,
        },
    )

    assert response.status_code == status.HTTP_200_OK

    return response.json()


def test_csrf_endpoint_sets_readable_cookie(
    client: TestClient,
) -> None:
    response = client.get("/api/auth/csrf")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["csrf_token"]
    assert "fantasy_preauth_csrf" in client.cookies


def test_login_requires_csrf(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, password = login_credentials

    response = client.post(
        "/api/auth/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
        },
        json={
            "username": username,
            "password": password,
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {"detail": "CSRF validation failed."}


def test_login_rejects_untrusted_origin(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, password = login_credentials
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/auth/login",
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
        json={
            "username": username,
            "password": password,
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {"detail": "Request origin is not allowed."}


def test_login_me_and_logout_flow(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, password = login_credentials

    payload = login(
        client,
        username=username,
        password=password,
    )

    assert payload["user"]["username"] == username
    assert payload["user"]["role"] == "spectator"
    assert "fantasy_session" in client.cookies
    assert "fantasy_session_csrf" in client.cookies

    me_response = client.get("/api/auth/me")

    assert me_response.status_code == status.HTTP_200_OK
    assert me_response.json()["username"] == username

    logout_response = client.post(
        "/api/auth/logout",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": payload["csrf_token"],
        },
    )

    assert logout_response.status_code == status.HTTP_200_OK
    assert logout_response.json() == {"logged_out": True}

    me_after_logout = client.get("/api/auth/me")

    assert me_after_logout.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_rejects_invalid_password(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, _ = login_credentials
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/auth/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "username": username,
            "password": "incorrect password",
        },
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": "Invalid username or password."}


def failed_login(
    client: TestClient,
    *,
    username: str,
    password: str = "incorrect password",
) -> object:
    csrf_token = issue_csrf(client)

    return client.post(
        "/api/auth/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "username": username,
            "password": password,
        },
    )


def test_first_two_failed_logins_return_generic_unauthorized(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, _ = login_credentials

    first = failed_login(
        client,
        username=username,
    )
    second = failed_login(
        client,
        username=username,
    )

    for response in (first, second):
        assert response.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert response.json() == {
            "detail": "Invalid username or password.",
        }
        assert "Retry-After" not in response.headers


def test_third_failed_login_is_temporarily_locked(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, _ = login_credentials

    first = failed_login(
        client,
        username=username,
    )
    second = failed_login(
        client,
        username=username,
    )
    third = failed_login(
        client,
        username=username,
    )

    assert first.status_code == status.HTTP_401_UNAUTHORIZED
    assert second.status_code == status.HTTP_401_UNAUTHORIZED
    assert third.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)
    assert third.json() == {
        "detail": "Invalid username or password.",
    }

    retry_after = int(third.headers["Retry-After"])

    assert 1 <= retry_after <= 900


def test_correct_password_is_rejected_during_lockout(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, password = login_credentials

    for _ in range(3):
        failed_login(
            client,
            username=username,
        )

    response = failed_login(
        client,
        username=username,
        password=password,
    )

    assert response.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)
    assert response.json() == {
        "detail": "Invalid username or password.",
    }


def test_successful_login_clears_prior_failures(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, password = login_credentials

    first_failure = failed_login(
        client,
        username=username,
    )

    assert first_failure.status_code == (status.HTTP_401_UNAUTHORIZED)

    payload = login(
        client,
        username=username,
        password=password,
    )

    assert payload["user"]["username"] == username

    logout_response = client.post(
        "/api/auth/logout",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": payload["csrf_token"],
        },
    )

    assert logout_response.status_code == status.HTTP_200_OK

    next_failure = failed_login(
        client,
        username=username,
    )

    assert next_failure.status_code == (status.HTTP_401_UNAUTHORIZED)
    assert "Retry-After" not in next_failure.headers


def test_unknown_username_uses_same_lockout_policy(
    client: TestClient,
) -> None:
    username = f"missing-{uuid4().hex[:12]}"

    first = failed_login(
        client,
        username=username,
    )
    second = failed_login(
        client,
        username=username,
    )
    third = failed_login(
        client,
        username=username,
    )

    assert first.status_code == status.HTTP_401_UNAUTHORIZED
    assert second.status_code == status.HTTP_401_UNAUTHORIZED
    assert third.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)
    assert third.json() == {
        "detail": "Invalid username or password.",
    }


def test_spectator_throttle_is_limited_to_one_browser_bucket(
    login_credentials: tuple[str, str],
) -> None:
    username, password = login_credentials

    with TestClient(app) as first_browser:
        first = failed_login(
            first_browser,
            username=username,
        )
        second = failed_login(
            first_browser,
            username=username,
        )
        third = failed_login(
            first_browser,
            username=username,
        )

        assert first.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert second.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert third.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)

        retry_after = int(third.headers["Retry-After"])

        assert 1 <= retry_after <= 30

        blocked_correct_password = failed_login(
            first_browser,
            username=username,
            password=password,
        )

        assert blocked_correct_password.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)

    with TestClient(app) as second_browser:
        payload = login(
            second_browser,
            username=username,
            password=password,
        )

        assert payload["user"]["username"] == username
        assert payload["user"]["role"] == "spectator"


def test_spectator_refresh_keeps_same_throttle_bucket(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    username, _ = login_credentials

    initial_csrf = issue_csrf(client)
    initial_browser_id = client.cookies.get("fantasy_login_browser")

    assert initial_csrf
    assert initial_browser_id

    first = failed_login(
        client,
        username=username,
    )

    refreshed_csrf = issue_csrf(client)
    refreshed_browser_id = client.cookies.get("fantasy_login_browser")

    assert refreshed_csrf
    assert refreshed_browser_id == initial_browser_id

    second = failed_login(
        client,
        username=username,
    )
    third = failed_login(
        client,
        username=username,
    )

    assert first.status_code == (status.HTTP_401_UNAUTHORIZED)
    assert second.status_code == (status.HTTP_401_UNAUTHORIZED)
    assert third.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)

    retry_after = int(third.headers["Retry-After"])

    assert 1 <= retry_after <= 30


def test_admin_throttle_is_account_wide_across_browsers(
    admin_login_credentials: tuple[str, str],
) -> None:
    username, password = admin_login_credentials

    with TestClient(app) as first_browser:
        first = failed_login(
            first_browser,
            username=username,
        )
        second = failed_login(
            first_browser,
            username=username,
        )
        third = failed_login(
            first_browser,
            username=username,
        )

        assert first.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert second.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert third.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)

        retry_after = int(third.headers["Retry-After"])

        assert 1 <= retry_after <= 15

    with TestClient(app) as second_browser:
        second_browser_csrf = issue_csrf(second_browser)

        response = second_browser.post(
            "/api/auth/login",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": second_browser_csrf,
            },
            json={
                "username": username,
                "password": password,
            },
        )

        assert response.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)
        assert response.json() == {
            "detail": "Invalid username or password.",
        }

        retry_after = int(response.headers["Retry-After"])

        assert 1 <= retry_after <= 15


def test_unknown_username_throttle_is_client_specific(
    client: TestClient,
) -> None:
    username = f"missing-client-{uuid4().hex[:12]}"

    try:
        first = failed_login(
            client,
            username=username,
        )
        second = failed_login(
            client,
            username=username,
        )
        third = failed_login(
            client,
            username=username,
        )

        assert first.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert second.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert third.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)

        retry_after = int(third.headers["Retry-After"])

        assert 1 <= retry_after <= 30

        with TestClient(app) as another_browser:
            other_first = failed_login(
                another_browser,
                username=username,
            )

            assert other_first.status_code == (status.HTTP_401_UNAUTHORIZED)
            assert "Retry-After" not in other_first.headers
    finally:
        clear_username_throttle_rows(username)


def test_twentieth_network_failure_throttles_source(
    client: TestClient,
) -> None:
    responses = []

    for index in range(20):
        responses.append(
            failed_login(
                client,
                username=(f"spray-target-{index:02d}"),
            )
        )

    for response in responses[:19]:
        assert response.status_code == (status.HTTP_401_UNAUTHORIZED)
        assert "Retry-After" not in response.headers

    twentieth = responses[19]

    assert twentieth.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)
    assert twentieth.json() == {
        "detail": "Invalid username or password.",
    }

    retry_after = int(twentieth.headers["Retry-After"])

    assert 1 <= retry_after <= 1800


def test_network_throttle_blocks_other_username(
    client: TestClient,
    login_credentials: tuple[str, str],
) -> None:
    valid_username, valid_password = login_credentials

    for index in range(20):
        failed_login(
            client,
            username=f"spray-other-{index:02d}",
        )

    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/auth/login",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "username": valid_username,
            "password": valid_password,
        },
    )

    assert response.status_code == (status.HTTP_429_TOO_MANY_REQUESTS)
    assert response.json() == {
        "detail": "Invalid username or password.",
    }

    retry_after = int(response.headers["Retry-After"])

    assert 1 <= retry_after <= 1800
