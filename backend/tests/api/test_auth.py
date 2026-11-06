from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import (
    generate_invitation_token,
    hash_token,
)
from app.db.auth_models import (
    InvitationStatus,
    RegistrationInvite,
)
from app.db.models import League, Season, Team
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


@pytest.fixture
def invitation_token() -> str:
    raw_token = generate_invitation_token()

    with SessionFactory() as session:
        season = session.scalar(
            select(Season)
            .join(
                League,
                Season.league_id == League.id,
            )
            .where(
                League.espn_league_id == 529250970,
                Season.year == 2026,
            )
        )

        assert season is not None

        team = session.scalar(
            select(Team).where(
                Team.season_id == season.id,
                Team.espn_team_id == 2,
            )
        )

        assert team is not None

        invitation = RegistrationInvite(
            token_hash=hash_token(raw_token),
            season_id=season.id,
            team_id=team.id,
            intended_display_name="Invited Test User",
            status=InvitationStatus.ACTIVE,
            created_at=datetime.now(UTC),
            expires_at=(datetime.now(UTC) + timedelta(hours=1)),
        )

        session.add(invitation)
        session.commit()

        invitation_id = invitation.id

    yield raw_token

    with SessionFactory() as session:
        stored = session.get(
            RegistrationInvite,
            invitation_id,
        )

        if stored is not None:
            if stored.used_by_user_id is not None:
                used_user = AuthenticationRepository(session).get_user_by_id(stored.used_by_user_id)

                if used_user is not None:
                    session.delete(used_user)

            session.delete(stored)
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
    assert payload["user"]["role"] == "member"
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


def test_registration_invitation_can_be_validated(
    client: TestClient,
    invitation_token: str,
) -> None:
    response = client.get(f"/api/auth/invitations/{invitation_token}")

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["valid"] is True
    assert payload["team_id"] == 2
    assert payload["season"] == 2026
    assert payload["intended_display_name"] == "Invited Test User"


def test_invited_member_can_register(
    client: TestClient,
    invitation_token: str,
) -> None:
    unique = uuid4().hex[:12]
    username = f"registered-{unique}"
    csrf_token = issue_csrf(client)

    response = client.post(
        "/api/auth/register",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json={
            "invitation_token": invitation_token,
            "username": username,
            "display_name": "Ignored Display Name",
            "email": None,
            "password": ("correct horse battery staple"),
        },
    )

    assert response.status_code == status.HTTP_201_CREATED

    payload = response.json()

    assert payload["user"]["username"] == username
    assert payload["user"]["display_name"] == "Invited Test User"
    assert payload["user"]["role"] == "member"
    assert payload["user"]["team_id"] == 2
    assert payload["user"]["season"] == 2026
    assert "fantasy_session" in client.cookies

    reused_client = TestClient(app)
    reused_csrf = issue_csrf(reused_client)

    reused_response = reused_client.post(
        "/api/auth/register",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": reused_csrf,
        },
        json={
            "invitation_token": invitation_token,
            "username": f"reused-{unique}",
            "display_name": "Reused User",
            "email": None,
            "password": ("correct horse battery staple"),
        },
    )

    assert reused_response.status_code == (status.HTTP_400_BAD_REQUEST)
