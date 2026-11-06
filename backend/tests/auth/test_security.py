import pytest

from app.auth.security import (
    CSRF_TOKEN_BYTES,
    INVITATION_TOKEN_BYTES,
    SESSION_TOKEN_BYTES,
    InvalidPasswordError,
    InvalidUsernameError,
    csrf_tokens_match,
    generate_csrf_token,
    generate_invitation_token,
    generate_session_token,
    hash_password,
    hash_token,
    normalize_username,
    tokens_match,
    validate_password,
    verify_password,
)


def test_password_is_hashed_with_argon2() -> None:
    password = "correct horse battery staple"
    encoded_hash = hash_password(password)

    assert encoded_hash.startswith("$argon2")
    assert password not in encoded_hash
    assert verify_password(password, encoded_hash)
    assert not verify_password(
        "incorrect password",
        encoded_hash,
    )


@pytest.mark.parametrize(
    "password",
    [
        "",
        "short",
        "elevenchars",
    ],
)
def test_short_password_is_rejected(
    password: str,
) -> None:
    with pytest.raises(InvalidPasswordError):
        validate_password(password)


def test_password_over_maximum_length_is_rejected() -> None:
    with pytest.raises(InvalidPasswordError):
        validate_password("x" * 129)


@pytest.mark.parametrize(
    ("provided", "expected"),
    [
        ("Henry", "henry"),
        ("  Henry.Huynh  ", "henry.huynh"),
        ("HENRY_007", "henry_007"),
        ("henry-007", "henry-007"),
    ],
)
def test_username_is_normalized(
    provided: str,
    expected: str,
) -> None:
    assert normalize_username(provided) == expected


@pytest.mark.parametrize(
    "username",
    [
        "",
        "ab",
        ".henry",
        "henry.",
        "henry huynh",
        "henry@example.com",
        "x" * 51,
    ],
)
def test_invalid_username_is_rejected(
    username: str,
) -> None:
    with pytest.raises(InvalidUsernameError):
        normalize_username(username)


def test_session_tokens_are_random_and_hashable() -> None:
    first = generate_session_token()
    second = generate_session_token()

    assert first != second
    assert len(first) >= SESSION_TOKEN_BYTES
    assert len(hash_token(first)) == 64
    assert tokens_match(first, hash_token(first))
    assert not tokens_match(second, hash_token(first))


def test_csrf_tokens_are_random_and_hashable() -> None:
    token = generate_csrf_token()
    token_hash = hash_token(token)

    assert len(token) >= CSRF_TOKEN_BYTES
    assert csrf_tokens_match(
        token,
        token,
        token_hash,
    )
    assert not csrf_tokens_match(
        token,
        "different-token",
        token_hash,
    )
    assert not csrf_tokens_match(
        None,
        token,
        token_hash,
    )
    assert not csrf_tokens_match(
        token,
        None,
        token_hash,
    )


def test_invitation_tokens_are_random_and_hashable() -> None:
    token = generate_invitation_token()

    assert len(token) >= INVITATION_TOKEN_BYTES
    assert tokens_match(token, hash_token(token))


def test_empty_tokens_are_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Token cannot be empty",
    ):
        hash_token("  ")

    assert not tokens_match("", "not-a-valid-hash")
