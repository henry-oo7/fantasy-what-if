import asyncio
import json

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.error_handlers import (
    GENERIC_SERVER_ERROR,
    unexpected_exception_handler,
    validation_exception_handler,
)
from app.main import app

TRUSTED_ORIGIN = "http://localhost:5173"


def request_for(
    path: str,
    *,
    method: str = "POST",
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": (
                "203.0.113.10",
                443,
            ),
            "server": (
                "fantasy.example.com",
                443,
            ),
        }
    )


def response_payload(response) -> dict:
    return json.loads(response.body.decode())


def test_validation_handler_removes_input_and_context() -> None:
    secret = "private-password-value"

    exception = RequestValidationError(
        [
            {
                "type": "string_too_long",
                "loc": (
                    "body",
                    "password",
                ),
                "msg": ("String should have at most 128 characters"),
                "input": secret,
                "ctx": {
                    "max_length": 128,
                    "private": secret,
                },
            }
        ]
    )

    response = asyncio.run(
        validation_exception_handler(
            request_for("/api/auth/login"),
            exception,
        )
    )

    payload = response_payload(response)
    serialized = response.body.decode()

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
    assert payload == {
        "detail": [
            {
                "type": "string_too_long",
                "loc": [
                    "body",
                    "password",
                ],
                "msg": ("String should have at most 128 characters"),
            }
        ]
    }
    assert secret not in serialized
    assert '"input"' not in serialized
    assert '"ctx"' not in serialized


def test_unexpected_handler_returns_generic_message() -> None:
    secret = "private-cookie-value"
    exception = RuntimeError(f"Database failure included {secret}")

    response = asyncio.run(
        unexpected_exception_handler(
            request_for(
                "/api/admin/sync",
            ),
            exception,
        )
    )

    serialized = response.body.decode()

    assert response.status_code == (status.HTTP_500_INTERNAL_SERVER_ERROR)
    assert response_payload(response) == {
        "detail": GENERIC_SERVER_ERROR,
    }
    assert secret not in serialized
    assert "Database failure" not in serialized
    assert "RuntimeError" not in serialized


def test_live_login_validation_does_not_echo_password() -> None:
    secret = "x" * 129

    with TestClient(app) as client:
        csrf_response = client.get("/api/auth/csrf")

        assert csrf_response.status_code == (status.HTTP_200_OK)

        csrf_token = csrf_response.json()["csrf_token"]

        response = client.post(
            "/api/auth/login",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token,
            },
            json={
                "username": "example-user",
                "password": secret,
            },
        )

    serialized = response.text

    assert response.status_code == (status.HTTP_422_UNPROCESSABLE_CONTENT)
    assert secret not in serialized
    assert '"input"' not in serialized
    assert '"ctx"' not in serialized
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_live_validation_identifies_field_without_value() -> None:
    secret = "private-value-" + ("x" * 140)

    with TestClient(app) as client:
        csrf_response = client.get("/api/auth/csrf")
        csrf_token = csrf_response.json()["csrf_token"]

        response = client.post(
            "/api/auth/login",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "X-CSRF-Token": csrf_token,
            },
            json={
                "username": "example-user",
                "password": secret,
            },
        )

    detail = response.json()["detail"]

    assert detail
    assert detail[0]["loc"] == [
        "body",
        "password",
    ]
    assert secret not in response.text
