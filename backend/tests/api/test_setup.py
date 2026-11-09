from fastapi import status
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_active_installation_status_is_public() -> None:
    response = client.get("/api/setup/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "active",
        "installation_exists": True,
        "bootstrap_available": False,
    }


TRUSTED_ORIGIN = "http://localhost:5173"


def issue_csrf() -> str:
    response = client.get("/api/auth/csrf")

    assert response.status_code == status.HTTP_200_OK

    return response.json()["csrf_token"]


def bootstrap_payload() -> dict[str, str]:
    return {
        "username": "bootstrap-admin",
        "display_name": "Bootstrap Administrator",
        "email": "bootstrap@example.com",
        "password": "correct horse battery staple",
    }


def test_bootstrap_requires_csrf() -> None:
    response = client.post(
        "/api/setup/bootstrap-admin",
        headers={
            "Origin": TRUSTED_ORIGIN,
        },
        json=bootstrap_payload(),
    )

    assert response.status_code == (status.HTTP_403_FORBIDDEN)
    assert response.json() == {
        "detail": "CSRF validation failed.",
    }


def test_bootstrap_rejects_untrusted_origin() -> None:
    csrf_token = issue_csrf()

    response = client.post(
        "/api/setup/bootstrap-admin",
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
        json=bootstrap_payload(),
    )

    assert response.status_code == (status.HTTP_403_FORBIDDEN)
    assert response.json() == {
        "detail": "Request origin is not allowed.",
    }


def test_active_installation_rejects_bootstrap() -> None:
    csrf_token = issue_csrf()

    response = client.post(
        "/api/setup/bootstrap-admin",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "X-CSRF-Token": csrf_token,
        },
        json=bootstrap_payload(),
    )

    assert response.status_code == (status.HTTP_409_CONFLICT)
    assert response.json() == {
        "detail": ("Administrator bootstrap is no longer available."),
    }


def test_setup_provider_discovery_is_public() -> None:
    response = client.get("/api/setup/providers")

    assert response.status_code == status.HTTP_200_OK

    assert response.json() == {
        "providers": [
            {
                "provider": "espn",
                "display_name": ("ESPN Fantasy Football"),
                "implemented": True,
                "requires_credentials": True,
                "credential_fields": [
                    "league_id",
                    "season",
                    "espn_s2",
                    "swid",
                ],
            },
            {
                "provider": "sleeper",
                "display_name": "Sleeper",
                "implemented": False,
                "requires_credentials": False,
                "credential_fields": [],
            },
            {
                "provider": "yahoo",
                "display_name": ("Yahoo Fantasy Sports"),
                "implemented": False,
                "requires_credentials": True,
                "credential_fields": [],
            },
        ],
    }


def test_provider_discovery_exposes_no_secrets() -> None:
    response = client.get("/api/setup/providers")

    serialized = response.text.casefold()

    assert "encrypted_credentials" not in serialized
    assert "provider_credential_key" not in serialized
    assert "private-cookie" not in serialized
    assert "environment-swid" not in serialized
