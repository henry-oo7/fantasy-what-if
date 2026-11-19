import pytest
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings


def create_settings(
    **overrides,
) -> Settings:
    values = {
        "app_env": "test",
        "app_debug": True,
        "app_secret_key": SecretStr("x" * 48),
        "frontend_origin": "http://localhost:5173",
        "trusted_origins": "http://localhost:5173",
        "session_cookie_name": "fantasy_session",
        "session_cookie_secure": False,
        "database_url": ("postgresql+psycopg://user:password@database:5432/fantasy"),
    }
    values.update(overrides)

    return Settings(**values)


def test_background_worker_defaults_are_conservative() -> None:
    settings = create_settings()

    assert settings.background_worker_enabled is False
    assert settings.background_worker_poll_seconds == 5
    assert settings.background_worker_lease_seconds == 300
    assert settings.background_worker_heartbeat_seconds == 60
    assert settings.background_worker_database_backoff_seconds == 10
    assert settings.background_worker_id is None


def test_background_worker_settings_accept_valid_overrides() -> None:
    settings = create_settings(
        background_worker_enabled=True,
        background_worker_poll_seconds=2,
        background_worker_lease_seconds=600,
        background_worker_heartbeat_seconds=90,
        background_worker_database_backoff_seconds=15,
        background_worker_id=" worker-dev-01 ",
    )

    assert settings.background_worker_enabled is True
    assert settings.background_worker_poll_seconds == 2
    assert settings.background_worker_lease_seconds == 600
    assert settings.background_worker_heartbeat_seconds == 90
    assert settings.background_worker_database_backoff_seconds == 15
    assert settings.background_worker_id == "worker-dev-01"


@pytest.mark.parametrize(
    (
        "field",
        "value",
    ),
    [
        (
            "background_worker_poll_seconds",
            0,
        ),
        (
            "background_worker_lease_seconds",
            29,
        ),
        (
            "background_worker_database_backoff_seconds",
            0,
        ),
    ],
)
def test_background_worker_rejects_unsafe_values(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        create_settings(
            **{
                field: value,
            }
        )


def test_blank_background_worker_id_uses_generated_identity() -> None:
    settings = create_settings(
        background_worker_id="   ",
    )

    assert settings.background_worker_id is None


def test_background_worker_id_has_database_length_limit() -> None:
    with pytest.raises(
        ValidationError,
        match="cannot exceed 255",
    ):
        create_settings(
            background_worker_id="x" * 256,
        )


@pytest.mark.parametrize(
    (
        "lease_seconds",
        "heartbeat_seconds",
    ),
    [
        (
            300,
            300,
        ),
        (
            300,
            301,
        ),
        (
            30,
            30,
        ),
    ],
)
def test_heartbeat_must_be_shorter_than_lease(
    lease_seconds: int,
    heartbeat_seconds: int,
) -> None:
    with pytest.raises(
        ValidationError,
        match="must be shorter",
    ):
        create_settings(
            background_worker_lease_seconds=(lease_seconds),
            background_worker_heartbeat_seconds=(heartbeat_seconds),
        )
