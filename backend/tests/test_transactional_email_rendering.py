"""Deterministic, escaped in-memory email rendering (ADR 0009)."""

from datetime import date, datetime

import pytest

from app.db.platform_enums import TransactionalEmailEventType as Event
from app.notifications.email_rendering import (
    EmailRenderingError,
    allowed_render_fields,
    render_transactional_email,
)

BASE_URL = "https://fantasy.synthetic.invalid"
TOKEN = "Synthetic-Render_Token0123456789abcdef"

CONTEXTS: dict[Event, dict[str, object]] = {
    Event.ADMINISTRATOR_EMAIL_VERIFICATION: {"token": TOKEN, "expires_hours": 24},
    Event.ADMINISTRATOR_PASSWORD_RESET: {"token": TOKEN, "expires_minutes": 30},
    Event.ADMINISTRATOR_PASSWORD_CHANGED: {},
    Event.WORKSPACE_DELETION_REQUESTED: {
        "requested_on": date(2030, 1, 2),
        "scheduled_for": date(2030, 2, 1),
    },
    Event.WORKSPACE_DELETION_REMINDER: {"scheduled_for": date(2030, 2, 1)},
    Event.WORKSPACE_DELETION_COMPLETED: {},
    Event.WORKSPACE_DELETION_CANCELED: {},
}


def test_every_approved_event_has_an_allowlist() -> None:
    assert set(CONTEXTS) == set(Event)

    for event, context in CONTEXTS.items():
        assert allowed_render_fields(event) == frozenset(context)


@pytest.mark.parametrize("event", list(Event))
def test_every_event_renders_deterministically(event: Event) -> None:
    first = render_transactional_email(event, public_base_url=BASE_URL, fields=CONTEXTS[event])
    second = render_transactional_email(event, public_base_url=BASE_URL, fields=CONTEXTS[event])

    assert first == second
    assert first.subject and first.text_body and first.html_body.startswith("<!doctype html>")
    assert TOKEN not in first.subject
    assert TOKEN not in repr(first)


@pytest.mark.parametrize(
    ("event", "path"),
    [
        (Event.ADMINISTRATOR_EMAIL_VERIFICATION, "/verify-email"),
        (Event.ADMINISTRATOR_PASSWORD_RESET, "/reset-password"),
    ],
)
def test_identity_links_use_public_base_url_and_encoded_token(event: Event, path: str) -> None:
    rendered = render_transactional_email(
        event, public_base_url=BASE_URL + "/", fields=CONTEXTS[event]
    )
    link = f"{BASE_URL}{path}?token={TOKEN}"

    assert link in rendered.text_body
    assert f'href="{link}"' in rendered.html_body
    assert TOKEN not in rendered.subject


def test_token_is_url_encoded_and_unsafe_tokens_are_rejected() -> None:
    with pytest.raises(EmailRenderingError):
        render_transactional_email(
            Event.ADMINISTRATOR_EMAIL_VERIFICATION,
            public_base_url=BASE_URL,
            fields={"token": 'abc"><script>alert(1)</script>xxxxxxxx', "expires_hours": 24},
        )

    with pytest.raises(EmailRenderingError):
        render_transactional_email(
            Event.ADMINISTRATOR_PASSWORD_RESET,
            public_base_url=BASE_URL,
            fields={"token": "short", "expires_minutes": 30},
        )


def test_singular_expiry_and_no_markup_injection() -> None:
    rendered = render_transactional_email(
        Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        public_base_url="https://fantasy.synthetic.invalid",
        fields={"token": TOKEN, "expires_hours": 1},
    )

    assert "1 hour " in rendered.text_body
    assert "<script" not in rendered.html_body


def test_href_attribute_is_escaped() -> None:
    from app.notifications import email_rendering

    rendered = email_rendering._compose(
        subject="S",
        paragraphs=("<b>x</b>",),
        closing='"quoted" & <tag>',
        link='https://a.invalid/?a=1&b="2"',
        link_label="<Label>",
    )

    assert "<b>x</b>" not in rendered.html_body
    assert "&lt;b&gt;x&lt;/b&gt;" in rendered.html_body
    assert 'href="https://a.invalid/?a=1&amp;b=&quot;2&quot;"' in rendered.html_body
    assert "&lt;Label&gt;" in rendered.html_body
    assert "&quot;quoted&quot; &amp; &lt;tag&gt;" in rendered.html_body


@pytest.mark.parametrize(
    ("event", "fields"),
    [
        (Event.ADMINISTRATOR_PASSWORD_CHANGED, {"display_name": "x"}),
        (Event.ADMINISTRATOR_EMAIL_VERIFICATION, {"token": TOKEN}),
        (
            Event.ADMINISTRATOR_EMAIL_VERIFICATION,
            {"token": TOKEN, "expires_hours": 24, "workspace_id": 1},
        ),
        (Event.WORKSPACE_DELETION_COMPLETED, {"provider_league_id": "529250970"}),
        (
            Event.WORKSPACE_DELETION_REQUESTED,
            {"requested_on": date(2030, 1, 2), "scheduled_for": "2030-02-01"},
        ),
        (
            Event.WORKSPACE_DELETION_REQUESTED,
            {"requested_on": date(2030, 1, 2), "scheduled_for": datetime(2030, 2, 1)},
        ),
        (Event.ADMINISTRATOR_PASSWORD_RESET, {"token": TOKEN, "expires_minutes": 0}),
        (Event.ADMINISTRATOR_PASSWORD_RESET, {"token": TOKEN, "expires_minutes": True}),
    ],
)
def test_fields_outside_the_allowlist_or_invalid_are_rejected(event: Event, fields: dict) -> None:
    with pytest.raises(EmailRenderingError):
        render_transactional_email(event, public_base_url=BASE_URL, fields=fields)


def test_invalid_public_base_url_is_rejected() -> None:
    with pytest.raises(EmailRenderingError):
        render_transactional_email(
            Event.ADMINISTRATOR_PASSWORD_CHANGED, public_base_url="", fields={}
        )


@pytest.mark.parametrize(
    "event",
    [
        Event.WORKSPACE_DELETION_REQUESTED,
        Event.WORKSPACE_DELETION_REMINDER,
        Event.WORKSPACE_DELETION_COMPLETED,
        Event.WORKSPACE_DELETION_CANCELED,
    ],
)
def test_deletion_notices_contain_no_internal_identifiers(event: Event) -> None:
    rendered = render_transactional_email(event, public_base_url=BASE_URL, fields=CONTEXTS[event])
    combined = f"{rendered.subject}\n{rendered.text_body}\n{rendered.html_body}".casefold()

    for forbidden in ("league id", "username", "password", "credential", "job", "slug", "token"):
        assert forbidden not in combined
    assert "http" not in rendered.text_body


DELETION_EVENTS = [
    Event.WORKSPACE_DELETION_REQUESTED,
    Event.WORKSPACE_DELETION_REMINDER,
    Event.WORKSPACE_DELETION_COMPLETED,
    Event.WORKSPACE_DELETION_CANCELED,
]


def test_requested_notice_renders_both_dates_as_iso_and_subject_is_generic() -> None:
    rendered = render_transactional_email(
        Event.WORKSPACE_DELETION_REQUESTED,
        public_base_url=BASE_URL,
        fields=CONTEXTS[Event.WORKSPACE_DELETION_REQUESTED],
    )

    for body in (rendered.text_body, rendered.html_body):
        assert "2030-01-02" in body
        assert "2030-02-01" in body
    assert not any(character.isdigit() for character in rendered.subject)
    assert "synthetic" not in rendered.subject.casefold()


@pytest.mark.parametrize(
    ("event", "fields"),
    [
        (
            Event.WORKSPACE_DELETION_REQUESTED,
            {"requested_on": datetime(2030, 1, 2), "scheduled_for": date(2030, 2, 1)},
        ),
        (
            Event.WORKSPACE_DELETION_REQUESTED,
            {"requested_on": date(2030, 1, 2), "scheduled_for": datetime(2030, 2, 1)},
        ),
        (Event.WORKSPACE_DELETION_REQUESTED, {"scheduled_for": date(2030, 2, 1)}),
        (Event.WORKSPACE_DELETION_REQUESTED, {"requested_on": date(2030, 1, 2)}),
        (
            Event.WORKSPACE_DELETION_REQUESTED,
            {
                "requested_on": date(2030, 1, 2),
                "scheduled_for": date(2030, 2, 1),
                "extra": 1,
            },
        ),
        (Event.WORKSPACE_DELETION_REMINDER, {"scheduled_for": datetime(2030, 2, 1)}),
        (Event.WORKSPACE_DELETION_REMINDER, {}),
        (
            Event.WORKSPACE_DELETION_REMINDER,
            {"scheduled_for": date(2030, 2, 1), "requested_on": date(2030, 1, 2)},
        ),
    ],
)
def test_requested_and_reminder_reject_datetimes_and_wrong_fields(
    event: Event, fields: dict
) -> None:
    with pytest.raises(EmailRenderingError):
        render_transactional_email(event, public_base_url=BASE_URL, fields=fields)


@pytest.mark.parametrize(
    "event", [Event.WORKSPACE_DELETION_COMPLETED, Event.WORKSPACE_DELETION_CANCELED]
)
def test_completed_and_canceled_accept_only_empty_fields(event: Event) -> None:
    assert allowed_render_fields(event) == frozenset()
    render_transactional_email(event, public_base_url=BASE_URL, fields={})

    with pytest.raises(EmailRenderingError):
        render_transactional_email(
            event, public_base_url=BASE_URL, fields={"scheduled_for": date(2030, 2, 1)}
        )


def test_completed_notice_contains_no_digits() -> None:
    rendered = render_transactional_email(
        Event.WORKSPACE_DELETION_COMPLETED, public_base_url=BASE_URL, fields={}
    )
    body_markup = rendered.html_body.split("<body>", 1)[1]

    for text in (rendered.subject, rendered.text_body, body_markup):
        assert not any(character.isdigit() for character in text)


def test_requested_notice_tells_administrator_to_sign_in_again_after_cancel() -> None:
    rendered = render_transactional_email(
        Event.WORKSPACE_DELETION_REQUESTED,
        public_base_url=BASE_URL,
        fields=CONTEXTS[Event.WORKSPACE_DELETION_REQUESTED],
    )

    assert "sign in again" in rendered.text_body


@pytest.mark.parametrize("event", DELETION_EVENTS)
def test_deletion_notices_never_name_providers_or_internal_terms(event: Event) -> None:
    rendered = render_transactional_email(event, public_base_url=BASE_URL, fields=CONTEXTS[event])
    combined = f"{rendered.subject}\n{rendered.text_body}\n{rendered.html_body}".casefold()

    for forbidden in ("espn", "sleeper", "yahoo", "league id", "username", "job", "password"):
        assert forbidden not in combined
