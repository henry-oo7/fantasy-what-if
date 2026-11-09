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
