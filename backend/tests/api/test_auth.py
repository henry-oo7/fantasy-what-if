from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import (
    AuthenticationRepository,
)

TRUSTED_ORIGIN = "http://localhost:5173"


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
