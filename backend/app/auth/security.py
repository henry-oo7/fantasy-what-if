import hashlib
import hmac
import re
from secrets import token_urlsafe

from pwdlib import PasswordHash

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 50

USERNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")

SESSION_TOKEN_BYTES = 48
CSRF_TOKEN_BYTES = 32
INVITATION_TOKEN_BYTES = 32


_password_hash = PasswordHash.recommended()


class InvalidPasswordError(ValueError):
    pass


class InvalidUsernameError(ValueError):
    pass


def normalize_username(username: str) -> str:
    normalized = username.strip().casefold()

    if not (USERNAME_MIN_LENGTH <= len(normalized) <= USERNAME_MAX_LENGTH):
        raise InvalidUsernameError(
            "Username must contain between "
            f"{USERNAME_MIN_LENGTH} and "
            f"{USERNAME_MAX_LENGTH} characters."
        )

    if not USERNAME_PATTERN.fullmatch(normalized):
        raise InvalidUsernameError(
            "Username may contain lowercase letters, "
            "numbers, periods, underscores, and hyphens. "
            "It must begin and end with a letter or number."
        )

    return normalized


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise InvalidPasswordError(
            f"Password must contain at least {PASSWORD_MIN_LENGTH} characters."
        )

    if len(password) > PASSWORD_MAX_LENGTH:
        raise InvalidPasswordError(
            f"Password cannot contain more than {PASSWORD_MAX_LENGTH} characters."
        )


def hash_password(password: str) -> str:
    validate_password(password)

    return _password_hash.hash(password)


def verify_password(
    password: str,
    encoded_hash: str,
) -> bool:
    try:
        return _password_hash.verify(
            password,
            encoded_hash,
        )
    except Exception:
        return False


def password_hash_needs_update(
    encoded_hash: str,
) -> bool:
    try:
        return _password_hash.check_needs_rehash(encoded_hash)
    except Exception:
        return True


def generate_session_token() -> str:
    return token_urlsafe(SESSION_TOKEN_BYTES)


def generate_csrf_token() -> str:
    return token_urlsafe(CSRF_TOKEN_BYTES)


def hash_token(token: str) -> str:
    normalized = token.strip()

    if not normalized:
        raise ValueError("Token cannot be empty.")

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hmac_identifier(
    value: str,
    *,
    secret: str,
    namespace: str,
) -> str:
    """Create a stable, non-reversible identifier."""

    normalized_value = value.strip().casefold()
    normalized_namespace = namespace.strip()
    normalized_secret = secret.strip()

    if not normalized_value:
        normalized_value = "unknown"

    if not normalized_namespace:
        raise ValueError("HMAC namespace cannot be empty.")

    if not normalized_secret:
        raise ValueError("HMAC secret cannot be empty.")

    message = (f"{normalized_namespace}:{normalized_value}").encode()

    return hmac.new(
        normalized_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def tokens_match(
    provided_token: str,
    expected_hash: str,
) -> bool:
    try:
        provided_hash = hash_token(provided_token)
    except ValueError:
        return False

    return hmac.compare_digest(
        provided_hash,
        expected_hash,
    )


def csrf_tokens_match(
    cookie_token: str | None,
    header_token: str | None,
    expected_hash: str,
) -> bool:
    if not cookie_token or not header_token:
        return False

    if not hmac.compare_digest(
        cookie_token,
        header_token,
    ):
        return False

    return tokens_match(
        header_token,
        expected_hash,
    )
