"""Deterministic in-memory rendering of transactional email.

Each event accepts an explicit allowlist of dynamic fields. Rendered
subjects, bodies, and links are never persisted or logged. Tokens appear
only URL-encoded inside the link in the body, never in a subject. Deletion
notices carry no league ID, credential, username, database ID, or job state.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape
from urllib.parse import quote

from app.db.platform_enums import TransactionalEmailEventType as Event

PRODUCT_NAME = "Fantasy What-If"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,256}$")


class EmailRenderingError(ValueError):
    """The event or its context cannot be rendered safely."""


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str = field(repr=False)
    text_body: str = field(repr=False)
    html_body: str = field(repr=False)


_ALLOWED_FIELDS: dict[Event, frozenset[str]] = {
    Event.ADMINISTRATOR_EMAIL_VERIFICATION: frozenset({"token", "expires_hours"}),
    Event.ADMINISTRATOR_PASSWORD_RESET: frozenset({"token", "expires_minutes"}),
    Event.ADMINISTRATOR_PASSWORD_CHANGED: frozenset(),
    Event.WORKSPACE_DELETION_REQUESTED: frozenset({"requested_on", "scheduled_for"}),
    Event.WORKSPACE_DELETION_REMINDER: frozenset({"scheduled_for"}),
    Event.WORKSPACE_DELETION_COMPLETED: frozenset(),
    Event.WORKSPACE_DELETION_CANCELED: frozenset(),
}


def allowed_render_fields(event_type: Event) -> frozenset[str]:
    return _ALLOWED_FIELDS[event_type]


def render_transactional_email(
    event_type: Event,
    *,
    public_base_url: str,
    fields: Mapping[str, object] | None = None,
) -> RenderedEmail:
    values = dict(fields or {})
    allowed = _ALLOWED_FIELDS.get(event_type)

    if allowed is None:
        raise EmailRenderingError("The email event type is not supported.")

    if set(values) != allowed:
        raise EmailRenderingError("The email context does not match the event allowlist.")

    base_url = public_base_url.rstrip("/")

    if not base_url.startswith(("https://", "http://")):
        raise EmailRenderingError("The public base URL is invalid.")

    match event_type:
        case Event.ADMINISTRATOR_EMAIL_VERIFICATION:
            link = _token_link(base_url, "/verify-email", values["token"])
            hours = _positive_int(values["expires_hours"])
            return _compose(
                subject=f"Verify your {PRODUCT_NAME} email address",
                paragraphs=(
                    "Confirm your administrator email address to continue setting up "
                    "your league workspace.",
                ),
                link=link,
                link_label="Verify email address",
                closing=(
                    f"This link expires in {hours} {_plural(hours, 'hour')} and can be "
                    "used once. If you did not register, you can ignore this message."
                ),
            )
        case Event.ADMINISTRATOR_PASSWORD_RESET:
            link = _token_link(base_url, "/reset-password", values["token"])
            minutes = _positive_int(values["expires_minutes"])
            return _compose(
                subject=f"Reset your {PRODUCT_NAME} password",
                paragraphs=("A password reset was requested for your administrator account.",),
                link=link,
                link_label="Reset password",
                closing=(
                    f"This link expires in {minutes} {_plural(minutes, 'minute')} and can "
                    "be used once. If you did not request a reset, you can ignore this "
                    "message; your password has not changed."
                ),
            )
        case Event.ADMINISTRATOR_PASSWORD_CHANGED:
            return _compose(
                subject=f"Your {PRODUCT_NAME} password was changed",
                paragraphs=(
                    "The password for your administrator account was changed and other "
                    "sessions were signed out.",
                ),
                closing=(
                    "If you did not make this change, reset your password immediately "
                    "from the sign-in page."
                ),
            )
        case Event.WORKSPACE_DELETION_REQUESTED:
            requested = _date_text(values["requested_on"])
            scheduled = _date_text(values["scheduled_for"])
            return _compose(
                subject=f"Your {PRODUCT_NAME} workspace is scheduled for deletion",
                paragraphs=(
                    f"Deletion of your league workspace was requested on {requested} "
                    f"(UTC). It is scheduled to be permanently deleted on {scheduled} "
                    "(UTC).",
                ),
                closing=(
                    "Sign in before then if you want to cancel the deletion. "
                    "After you cancel, sign in again to continue using your workspace."
                ),
            )
        case Event.WORKSPACE_DELETION_REMINDER:
            scheduled = _date_text(values["scheduled_for"])
            return _compose(
                subject=f"Reminder: your {PRODUCT_NAME} workspace will be deleted soon",
                paragraphs=(
                    f"Your league workspace is scheduled to be permanently deleted on "
                    f"{scheduled} (UTC).",
                ),
                closing="Sign in before then if you want to cancel the deletion.",
            )
        case Event.WORKSPACE_DELETION_COMPLETED:
            return _compose(
                subject=f"Your {PRODUCT_NAME} workspace was deleted",
                paragraphs=("Your league workspace and its data were permanently deleted.",),
                closing="No further action is needed.",
            )
        case Event.WORKSPACE_DELETION_CANCELED:
            return _compose(
                subject=f"Your {PRODUCT_NAME} workspace deletion was canceled",
                paragraphs=("The scheduled deletion of your league workspace was canceled.",),
                closing="Your workspace remains available.",
            )

    raise EmailRenderingError("The email event type is not supported.")


def _token_link(base_url: str, path: str, token: object) -> str:
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise EmailRenderingError("The one-time link could not be built.")

    return f"{base_url}{path}?token={quote(token, safe='')}"


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EmailRenderingError("The email context is invalid.")

    return value


def _date_text(value: object) -> str:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise EmailRenderingError("The email context is invalid.")

    return value.isoformat()


def _plural(count: int, unit: str) -> str:
    return unit if count == 1 else f"{unit}s"


def _compose(
    *,
    subject: str,
    paragraphs: tuple[str, ...],
    closing: str,
    link: str | None = None,
    link_label: str | None = None,
) -> RenderedEmail:
    text_parts = [*paragraphs]

    if link is not None:
        text_parts.append(f"{link_label}:\n{link}")

    text_parts.append(closing)
    text_parts.append(f"— {PRODUCT_NAME}")

    html_parts = [f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs]

    if link is not None and link_label is not None:
        html_parts.append(
            f'<p><a href="{escape(link, quote=True)}">{escape(link_label)}</a></p>'
            f"<p>{escape(link)}</p>"
        )

    html_parts.append(f"<p>{escape(closing)}</p>")
    html_parts.append(f"<p>{escape(PRODUCT_NAME)}</p>")

    html_body = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{escape(subject)}</title></head><body>" + "".join(html_parts) + "</body></html>"
    )

    return RenderedEmail(
        subject=subject,
        text_body="\n\n".join(text_parts) + "\n",
        html_body=html_body,
    )
