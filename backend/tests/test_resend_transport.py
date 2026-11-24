"""Direct Resend transport against a fake httpx transport (no network)."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.notifications.email_provider import (
    EmailAcceptance,
    EmailFailureCategory,
    EmailFailureDisposition,
    EmailProviderError,
    OutgoingEmail,
    provider_idempotency_key,
)
from app.notifications.resend_transport import (
    MAXIMUM_RESPONSE_BYTES,
    RESEND_EMAILS_ENDPOINT,
    ResendTransport,
    next_utc_day,
    next_utc_month,
    parse_retry_after,
)

API_KEY = "re_synthetic_transport_key_123456"
RECIPIENT = "owner-transport@synthetic.invalid"
TOKEN = "SyntheticTransportToken_abcdefghijklmnop"
NOW = datetime(2030, 3, 14, 15, 9, 26, tzinfo=UTC)
SECRETS = (API_KEY, RECIPIENT, TOKEN)


def message(key: str = "41:1") -> OutgoingEmail:
    return OutgoingEmail(
        recipient=RECIPIENT,
        sender_address="notifications@synthetic.invalid",
        sender_name="Fantasy What-If",
        subject="Verify your Fantasy What-If email address",
        text_body=f"https://fantasy.synthetic.invalid/verify-email?token={TOKEN}",
        html_body=f"<p>{TOKEN}</p>",
        idempotency_key=key,
    )


def make_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs,
) -> ResendTransport:
    return ResendTransport(
        api_key=SecretStr(API_KEY),
        transport=httpx.MockTransport(handler),
        now=lambda: NOW,
        **kwargs,
    )


def json_response(status: int, body: object, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def send_error(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> EmailProviderError:
    with pytest.raises(EmailProviderError) as caught:
        make_transport(handler, **kwargs).send(message())

    error = caught.value
    rendered = f"{error!s} {error!r} {error.args!r}"

    for secret in SECRETS:
        assert secret not in rendered

    assert error.__cause__ is None
    assert error.__suppress_context__ is True or error.__context__ is None
    return error


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("Unmocked network request attempted.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


def test_default_transport_refuses_unmocked_requests() -> None:
    transport = ResendTransport(api_key=SecretStr(API_KEY))

    with pytest.raises(EmailProviderError) as caught:
        transport.send(message())

    # The guard's AssertionError is absorbed into a safe category, proving
    # the request never left the process.
    assert caught.value.category is EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID


def test_success_posts_minimal_request_and_returns_message_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, {"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"})

    result = make_transport(handler).send(message("41:3"))

    assert result == EmailAcceptance(provider_message_id="49a3999c-0ce1-4ea6-ab68-afcd6dc2e794")
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == RESEND_EMAILS_ENDPOINT
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["idempotency-key"] == "41:3"
    body = json.loads(request.content)
    assert set(body) == {"from", "to", "subject", "text", "html"}
    assert body["to"] == [RECIPIENT]
    assert body["from"] == "Fantasy What-If <notifications@synthetic.invalid>"
    assert "tags" not in body and "headers" not in body


def test_timeouts_are_strict_and_redirects_disabled() -> None:
    transport = make_transport(lambda _r: json_response(200, {"id": "x"}))
    client = transport._client

    assert client.timeout.connect == 3
    assert client.timeout.read == 7
    assert client.timeout.write == 7
    assert client.timeout.pool == 3
    assert client.follow_redirects is False


def test_redirect_is_not_followed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://elsewhere.synthetic.invalid/"})

    error = send_error(handler)

    assert calls == [RESEND_EMAILS_ENDPOINT]
    assert error.category is EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID


def test_no_adapter_level_retries() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return json_response(503, {"name": "service_unavailable"})

    send_error(handler)
    assert calls == 1


def test_rate_limit_uses_bounded_retry_after() -> None:
    error = send_error(
        lambda _r: json_response(429, {"name": "rate_limit_exceeded"}, {"retry-after": "12"})
    )

    assert error.category is EmailFailureCategory.PROVIDER_RATE_LIMITED
    assert error.disposition is EmailFailureDisposition.DEFERRABLE
    assert error.retry_not_before == NOW + timedelta(seconds=12)


def test_rate_limit_retry_after_is_clamped() -> None:
    error = send_error(
        lambda _r: json_response(429, {"name": "rate_limit_exceeded"}, {"retry-after": "999999"}),
        maximum_deferral=timedelta(minutes=10),
    )

    assert error.retry_not_before == NOW + timedelta(minutes=10)


def test_rate_limit_without_retry_after_uses_default() -> None:
    error = send_error(lambda _r: json_response(429, {"name": "rate_limit_exceeded"}))

    assert error.retry_not_before == NOW + timedelta(seconds=60)


def test_daily_quota_defers_to_next_utc_day() -> None:
    error = send_error(lambda _r: json_response(429, {"name": "daily_quota_exceeded"}))

    assert error.category is EmailFailureCategory.PROVIDER_QUOTA_EXHAUSTED
    assert error.retry_not_before == datetime(2030, 3, 15, 0, 1, tzinfo=UTC)


def test_monthly_quota_defers_to_next_utc_month() -> None:
    error = send_error(lambda _r: json_response(429, {"name": "monthly_quota_exceeded"}))

    assert error.category is EmailFailureCategory.PROVIDER_QUOTA_EXHAUSTED
    assert error.retry_not_before == datetime(2030, 4, 1, 0, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "name", "category"),
    [
        (401, "validation_error", EmailFailureCategory.PROVIDER_AUTHENTICATION_FAILED),
        (401, "missing_api_key", EmailFailureCategory.PROVIDER_AUTHENTICATION_FAILED),
        (403, "restricted_api_key", EmailFailureCategory.PROVIDER_AUTHENTICATION_FAILED),
        (403, "suspended_api_key", EmailFailureCategory.PROVIDER_AUTHENTICATION_FAILED),
        (403, "validation_error", EmailFailureCategory.SENDER_NOT_VERIFIED),
        (400, "validation_error", EmailFailureCategory.RECIPIENT_INVALID),
        (400, "invalid_idempotency_key", EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID),
        (422, "missing_required_field", EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID),
        (404, "not_found", EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID),
        (409, "concurrent_idempotent_requests", EmailFailureCategory.PROVIDER_UNAVAILABLE),
        (409, "invalid_idempotent_request", EmailFailureCategory.PROVIDER_UNAVAILABLE),
        (500, "application_error", EmailFailureCategory.PROVIDER_UNAVAILABLE),
        (503, "service_unavailable", EmailFailureCategory.PROVIDER_UNAVAILABLE),
    ],
)
def test_error_classification(status: int, name: str, category: EmailFailureCategory) -> None:
    error = send_error(
        lambda _r: json_response(status, {"statusCode": status, "name": name, "message": RECIPIENT})
    )

    assert error.category is category


@pytest.mark.parametrize(
    ("category", "disposition"),
    [
        (EmailFailureCategory.PROVIDER_RATE_LIMITED, EmailFailureDisposition.DEFERRABLE),
        (EmailFailureCategory.PROVIDER_QUOTA_EXHAUSTED, EmailFailureDisposition.DEFERRABLE),
        (EmailFailureCategory.PROVIDER_UNAVAILABLE, EmailFailureDisposition.RETRYABLE),
        (EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN, EmailFailureDisposition.RETRYABLE),
        (EmailFailureCategory.PROVIDER_RESPONSE_INVALID, EmailFailureDisposition.RETRYABLE),
        (EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID, EmailFailureDisposition.PERMANENT),
        (EmailFailureCategory.SENDER_NOT_VERIFIED, EmailFailureDisposition.PERMANENT),
        (EmailFailureCategory.RECIPIENT_INVALID, EmailFailureDisposition.PERMANENT),
        (EmailFailureCategory.PROVIDER_AUTHENTICATION_FAILED, EmailFailureDisposition.PERMANENT),
    ],
)
def test_every_category_has_a_disposition(
    category: EmailFailureCategory, disposition: EmailFailureDisposition
) -> None:
    assert category.disposition is disposition


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (httpx.ConnectTimeout("connect"), EmailFailureCategory.PROVIDER_UNAVAILABLE),
        (httpx.ConnectError("refused"), EmailFailureCategory.PROVIDER_UNAVAILABLE),
        (httpx.PoolTimeout("pool"), EmailFailureCategory.PROVIDER_UNAVAILABLE),
        (httpx.ReadTimeout("read"), EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN),
        (httpx.WriteTimeout("write"), EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN),
        (httpx.RemoteProtocolError("reset"), EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN),
        (httpx.ReadError("reset"), EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN),
    ],
)
def test_transport_failures(exception: Exception, category: EmailFailureCategory) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise exception

    assert send_error(handler).category is category


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b'{"id": ""}',
        b'{"id": 42}',
        b'{"id": " spaced "}',
        b'{"message": "accepted"}',
    ],
)
def test_malformed_success_body_is_uncertain(body: bytes) -> None:
    error = send_error(lambda _r: httpx.Response(200, content=body))

    assert error.category is EmailFailureCategory.PROVIDER_RESPONSE_INVALID
    assert error.disposition is EmailFailureDisposition.RETRYABLE


def test_oversized_success_body_is_rejected_without_full_read() -> None:
    chunks_read = 0

    def stream():
        nonlocal chunks_read
        payload = b'{"id": "' + b"a" * 1024
        while True:
            chunks_read += 1
            yield payload

    error = send_error(lambda _r: httpx.Response(200, content=stream()))

    assert error.category is EmailFailureCategory.PROVIDER_RESPONSE_INVALID
    assert chunks_read <= MAXIMUM_RESPONSE_BYTES // 1024 + 2


def test_oversized_declared_length_is_rejected() -> None:
    error = send_error(
        lambda _r: httpx.Response(
            200,
            content=b"{}",
            headers={"content-length": str(MAXIMUM_RESPONSE_BYTES + 1)},
        )
    )

    assert error.category is EmailFailureCategory.PROVIDER_RESPONSE_INVALID


def test_nothing_secret_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    make_transport(lambda _r: json_response(200, {"id": "message-1"})).send(message())
    send_error(lambda _r: json_response(403, {"name": "validation_error", "message": RECIPIENT}))

    for secret in SECRETS:
        assert secret not in caplog.text


def test_outgoing_message_repr_hides_content() -> None:
    rendered = repr(message())

    assert RECIPIENT not in rendered
    assert TOKEN not in rendered


def test_api_key_is_required() -> None:
    with pytest.raises(ValueError):
        ResendTransport(api_key=SecretStr("  "))


def test_idempotency_key_uses_only_delivery_and_attempt() -> None:
    assert provider_idempotency_key(delivery_id=41, attempt=2) == "41:2"

    for bad in ((0, 1), (1, 0), (True, 1), (1, True)):
        with pytest.raises(ValueError):
            provider_idempotency_key(delivery_id=bad[0], attempt=bad[1])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, timedelta(seconds=60)),
        ("", timedelta(seconds=60)),
        ("abc", timedelta(seconds=60)),
        ("-5", timedelta(seconds=60)),
        ("0", timedelta(seconds=1)),
        ("30", timedelta(seconds=30)),
        ("99999999999", timedelta(seconds=60)),
        ("Thu, 14 Mar 2030 15:10:26 GMT", timedelta(seconds=60)),
        ("Thu, 14 Mar 2030 15:09:56 GMT", timedelta(seconds=30)),
        ("Thu, 14 Mar 2030 14:00:00 GMT", timedelta(seconds=1)),
        ("Fri, 15 Mar 2030 15:09:26 GMT", timedelta(hours=1)),
    ],
)
def test_retry_after_parsing_is_bounded(value: str | None, expected: timedelta) -> None:
    assert parse_retry_after(value, now=NOW, maximum=timedelta(hours=1)) == expected


def test_quota_boundaries() -> None:
    assert next_utc_day(datetime(2030, 12, 31, 23, 59, tzinfo=UTC)) == datetime(
        2031, 1, 1, tzinfo=UTC
    )
    assert next_utc_month(datetime(2030, 12, 5, tzinfo=UTC)) == datetime(2031, 1, 1, tzinfo=UTC)
