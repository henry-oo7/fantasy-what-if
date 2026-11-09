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
