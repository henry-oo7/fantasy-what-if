"""Synthetic helpers for durable administrator identity-email tests.

Public identity routes only queue deliveries. Tests that need a raw token
either run the real transactional-email worker against an in-memory fake
transport (``deliver_identity_email``) or create a legacy NULL-linked token
fixture directly through the repository (``issue_*_token``). Neither path
makes a network request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.security import generate_account_token
from app.db.auth_models import EmailVerificationToken, PasswordResetToken, User
from app.db.platform_enums import BackgroundJobType, TransactionalEmailEventType
from app.db.platform_models import BackgroundJob, TransactionalEmailDelivery
from app.db.session import SessionFactory
from app.notifications.delivery_job_contract import transactional_email_job_idempotency_key
from app.notifications.email_provider import EmailAcceptance, OutgoingEmail
from app.notifications.resend_transport import ResendTransport
from app.repositories.account_token_repository import AccountTokenRepository
from app.services.background_job_worker import snapshot_background_job
from app.services.transactional_email_delivery import TransactionalEmailDeliveryHandler
from tests.support.email_delivery import FakeTransport, email_settings, token_from

VERIFICATION = TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION
PASSWORD_RESET = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET
PASSWORD_CHANGED = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_CHANGED


@dataclass(frozen=True, slots=True)
class IssuedTestToken:
    """Legacy-style token fixture: plaintext held only by the test."""

    raw_token: str
    expires_at: datetime


def issue_email_verification_token(
    session: Session,
    *,
    user: User,
    lifetime_hours: int,
    now: datetime | None = None,
) -> IssuedTestToken:
    """Create a NULL-linked verification token fixture (not a product path)."""

    current = now or datetime.now(UTC)
    raw = generate_account_token()
    expires_at = current + timedelta(hours=lifetime_hours)
    AccountTokenRepository(session).create_email_verification_token(
        user_id=user.id, raw_token=raw, expires_at=expires_at, now=current
    )
    return IssuedTestToken(raw_token=raw, expires_at=expires_at)


def issue_password_reset_token(
    session: Session,
    *,
    user: User,
    lifetime_minutes: int,
    now: datetime | None = None,
) -> IssuedTestToken:
    """Create a NULL-linked password-reset token fixture (not a product path)."""

    current = now or datetime.now(UTC)
    raw = generate_account_token()
    expires_at = current + timedelta(minutes=lifetime_minutes)
    AccountTokenRepository(session).create_password_reset_token(
        user_id=user.id, raw_token=raw, expires_at=expires_at, now=current
    )
    return IssuedTestToken(raw_token=raw, expires_at=expires_at)


def forbid_provider_and_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail loudly if any code path reaches the provider transport or httpx."""

    calls: list[str] = []

    def refuse(name: str):
        def _refuse(*_args: object, **_kwargs: object):
            calls.append(name)
            raise AssertionError(f"Unexpected provider or network call: {name}")

        return _refuse

    monkeypatch.setattr(ResendTransport, "send", refuse("ResendTransport.send"))
    # TestClient uses its own in-process transport, so patching the real
    # network transports blocks external I/O without breaking API tests.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse("httpx.HTTPTransport"))
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", refuse("httpx.AsyncHTTPTransport")
    )
    return calls


def identity_deliveries(
    session: Session,
    user_id: int,
    event_type: TransactionalEmailEventType | None = None,
) -> list[TransactionalEmailDelivery]:
    statement = select(TransactionalEmailDelivery).where(
        TransactionalEmailDelivery.user_id == user_id
    )

    if event_type is not None:
        statement = statement.where(TransactionalEmailDelivery.event_type == event_type)

    return list(session.scalars(statement.order_by(TransactionalEmailDelivery.id)))


def email_jobs_for(session: Session, delivery_ids: list[int]) -> list[BackgroundJob]:
    if not delivery_ids:
        return []

    keys = [transactional_email_job_idempotency_key(delivery_id) for delivery_id in delivery_ids]
    return list(
        session.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY,
                BackgroundJob.idempotency_key.in_(keys),
            )
            .order_by(BackgroundJob.id)
        )
    )


def token_count(session: Session, user_id: int) -> int:
    verification = session.scalar(
        select(func.count())
        .select_from(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user_id)
    )
    reset = session.scalar(
        select(func.count())
        .select_from(PasswordResetToken)
        .where(PasswordResetToken.user_id == user_id)
    )
    return int(verification or 0) + int(reset or 0)


def deliver_identity_email(
    delivery_id: int,
    *,
    session_factory: sessionmaker[Session] = SessionFactory,
    message_id: str = "synthetic-provider-message",
) -> OutgoingEmail:
    """Run the real worker handler once for a delivery with a fake transport.

    Returns the in-memory message so a test can extract the worker-created
    token. The fake transport accepts exactly one send.
    """

    with session_factory() as session:
        jobs = email_jobs_for(session, [delivery_id])

        if len(jobs) != 1:
            raise AssertionError(f"Expected one email job for the delivery, found {len(jobs)}.")

        snapshot = snapshot_background_job(jobs[0])

    transport = FakeTransport([EmailAcceptance(provider_message_id=message_id)])
    TransactionalEmailDeliveryHandler(
        session_factory=session_factory,
        settings=email_settings(),
        transport_factory=transport.factory,
    )(snapshot)

    if len(transport.sent) != 1:
        raise AssertionError("The worker did not send exactly one message.")

    return transport.sent[0]


def deliver_and_extract_token(delivery_id: int, **kwargs: object) -> str:
    return token_from(deliver_identity_email(delivery_id, **kwargs))  # type: ignore[arg-type]
