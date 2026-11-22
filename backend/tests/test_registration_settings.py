import pytest
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings
from app.db.platform_enums import RegistrationMode


def create_settings(**overrides) -> Settings:
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


def test_registration_defaults_are_closed_and_bounded() -> None:
    settings = create_settings()

    assert settings.registration_mode is RegistrationMode.CLOSED
    assert settings.email_verification_lifetime_hours == 24
    assert settings.verification_resend_maximum_requests == 3
    assert settings.verification_resend_window_minutes == 60
    assert settings.password_reset_lifetime_minutes == 30
    assert settings.password_reset_maximum_requests == 3
    assert settings.password_reset_window_minutes == 60
    assert settings.registration_submission_maximum_requests == 5
    assert settings.registration_submission_window_minutes == 60


@pytest.mark.parametrize(
    "mode",
    [
        RegistrationMode.OPEN,
        RegistrationMode.CLOSED,
        RegistrationMode.MAINTENANCE,
        "open",
        "closed",
        "maintenance",
    ],
)
def test_registration_modes_are_accepted(
    mode: RegistrationMode | str,
) -> None:
    settings = create_settings(
        registration_mode=mode,
    )

    assert settings.registration_mode is RegistrationMode(str(mode))


def test_unknown_registration_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        create_settings(
            registration_mode="unsupported",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email_verification_lifetime_hours", 0),
        ("email_verification_lifetime_hours", 169),
        ("verification_resend_maximum_requests", 0),
        ("verification_resend_maximum_requests", 21),
        ("verification_resend_window_minutes", 0),
        ("verification_resend_window_minutes", 1441),
        ("password_reset_lifetime_minutes", 4),
        ("password_reset_lifetime_minutes", 1441),
        ("password_reset_maximum_requests", 0),
        ("password_reset_maximum_requests", 21),
        ("password_reset_window_minutes", 0),
        ("password_reset_window_minutes", 1441),
        ("registration_submission_maximum_requests", 0),
        ("registration_submission_maximum_requests", 51),
        ("registration_submission_window_minutes", 0),
        ("registration_submission_window_minutes", 1441),
    ],
)
def test_token_lifetimes_are_bounded(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        create_settings(
            **{
                field: value,
            }
        )
