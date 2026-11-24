"""Pure unit tests for identity-email idempotency-key builders (ADR 0009)."""

from __future__ import annotations

import pytest

from app.services.identity_email import (
    password_changed_key,
    password_reset_key,
    registration_verification_key,
    verification_resend_key,
)


def test_registration_verification_key_format() -> None:
    assert registration_verification_key(7) == (
        "identity-email:administrator_email_verification:user:7:registration"
    )


def test_verification_resend_key_format() -> None:
    assert verification_resend_key(7, 12) == (
        "identity-email:administrator_email_verification:user:7:resend-after:12"
    )
    assert verification_resend_key(7, 0) == (
        "identity-email:administrator_email_verification:user:7:resend-after:0"
    )


def test_password_reset_key_format() -> None:
    assert password_reset_key(7, 12) == (
        "identity-email:administrator_password_reset:user:7:after:12"
    )
    assert password_reset_key(7, 0) == "identity-email:administrator_password_reset:user:7:after:0"


def test_password_changed_key_format() -> None:
    assert password_changed_key(7, 31) == (
        "identity-email:administrator_password_changed:user:7:reset-token:31"
    )


def test_builders_are_deterministic() -> None:
    assert registration_verification_key(7) == registration_verification_key(7)
    assert verification_resend_key(7, 3) == verification_resend_key(7, 3)
    assert password_reset_key(7, 3) == password_reset_key(7, 3)
    assert password_changed_key(7, 3) == password_changed_key(7, 3)


def test_keys_are_distinct_across_kinds() -> None:
    keys = {
        registration_verification_key(7),
        verification_resend_key(7, 3),
        password_reset_key(7, 3),
        password_changed_key(7, 3),
    }

    assert len(keys) == 4


def test_keys_are_distinct_across_previous_ids_and_users() -> None:
    assert verification_resend_key(7, 3) != verification_resend_key(7, 4)
    assert password_reset_key(7, 3) != password_reset_key(7, 4)
    assert password_changed_key(7, 3) != password_changed_key(7, 4)
    assert registration_verification_key(7) != registration_verification_key(8)
    assert verification_resend_key(7, 3) != verification_resend_key(8, 3)
    assert password_reset_key(7, 3) != password_reset_key(8, 3)
    assert password_changed_key(7, 3) != password_changed_key(8, 3)


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.0, "1", None])
def test_user_id_must_be_a_positive_integer(invalid: object) -> None:
    with pytest.raises(ValueError, match="user_id"):
        registration_verification_key(invalid)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="user_id"):
        verification_resend_key(invalid, 1)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="user_id"):
        password_reset_key(invalid, 1)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="user_id"):
        password_changed_key(invalid, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.0, "1", None])
def test_reset_token_id_must_be_a_positive_integer(invalid: object) -> None:
    with pytest.raises(ValueError, match="reset_token_id"):
        password_changed_key(1, invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("builder", [verification_resend_key, password_reset_key])
def test_previous_delivery_id_accepts_zero_but_rejects_negative_and_bool(builder) -> None:
    assert builder(1, 0)

    for invalid in (-1, True, False, 1.0, "1", None):
        with pytest.raises(ValueError, match="previous_delivery_id"):
            builder(1, invalid)


def test_keys_never_contain_an_email_address() -> None:
    keys = [
        registration_verification_key(7),
        verification_resend_key(7, 3),
        password_reset_key(7, 3),
        password_changed_key(7, 3),
    ]

    assert all("@" not in key for key in keys)
