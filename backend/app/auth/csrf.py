import hashlib
import hmac
import time
from secrets import token_urlsafe

from app.core.settings import Settings

CSRF_HEADER_NAME = "X-CSRF-Token"
PRE_AUTH_CSRF_COOKIE_NAME = "fantasy_preauth_csrf"
PRE_AUTH_CSRF_LIFETIME_SECONDS = 60 * 60
CSRF_NONCE_BYTES = 32


def generate_pre_auth_csrf_token(
    settings: Settings,
    *,
    timestamp: int | None = None,
) -> str:
    issued_at = timestamp if timestamp is not None else int(time.time())
    nonce = token_urlsafe(CSRF_NONCE_BYTES)
    payload = f"{nonce}.{issued_at}"
    signature = _sign_payload(
        payload,
        settings=settings,
    )

    return f"{payload}.{signature}"


def validate_pre_auth_csrf_token(
    token: str,
    *,
    settings: Settings,
    now: int | None = None,
) -> bool:
    try:
        nonce, timestamp_text, signature = token.split(
            ".",
            maxsplit=2,
        )
        issued_at = int(timestamp_text)
    except TypeError, ValueError:
        return False

    if not nonce or not signature:
        return False

    current_time = now if now is not None else int(time.time())

    age = current_time - issued_at

    if age < 0:
        return False

    if age > PRE_AUTH_CSRF_LIFETIME_SECONDS:
        return False

    payload = f"{nonce}.{issued_at}"
    expected_signature = _sign_payload(
        payload,
        settings=settings,
    )

    return hmac.compare_digest(
        signature,
        expected_signature,
    )


def validate_double_submit_csrf(
    *,
    cookie_token: str | None,
    header_token: str | None,
    settings: Settings,
    now: int | None = None,
) -> bool:
    if not cookie_token or not header_token:
        return False

    if not hmac.compare_digest(
        cookie_token,
        header_token,
    ):
        return False

    return validate_pre_auth_csrf_token(
        header_token,
        settings=settings,
        now=now,
    )


def _sign_payload(
    payload: str,
    *,
    settings: Settings,
) -> str:
    secret = settings.app_secret_key.get_secret_value().encode("utf-8")

    return hmac.new(
        secret,
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
