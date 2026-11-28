"""BackgroundJob handler for durable transactional-email delivery (ADR 0009).

The delivery row is the only authority; the job payload names only the
delivery. Work runs in three phases so no database transaction is open
during provider HTTP:

1. Short locked transaction: verify job/delivery ownership and eligibility,
   resolve the recipient, persist a new attempt (the provider-attempt
   identity), and for identity events store a new token hash linked to the
   delivery and attempt. Commit.
2. In memory, outside any transaction: render, build the link, and send
   once through the transport with key ``{delivery_id}:{attempt}``.
3. Short locked transaction: if the same attempt is still current, record
   sent, retry-scheduled, or permanently-failed. Commit.

Token plaintext exists only in process memory between phases 1 and 2. An
uncertain outcome is retried as a new attempt with a new token; earlier
tokens of the delivery remain valid until a later accepted delivery
supersedes them or one token is consumed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.identity import IdentityKind, classify_identity
from app.auth.security import generate_account_token
from app.core.settings import Settings
from app.db.auth_models import User, UserRole
from app.db.platform_enums import (
    BackgroundJobStatus,
    OnboardingStage,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation, TransactionalEmailDelivery
from app.notifications.delivery_job_contract import validate_transactional_email_job_payload
from app.notifications.email_provider import (
    EmailAcceptance,
    EmailFailureCategory,
    EmailFailureDisposition,
    EmailProviderError,
    OutgoingEmail,
    TransactionalEmailTransport,
    provider_idempotency_key,
)
from app.notifications.email_rendering import (
    EmailRenderingError,
    render_transactional_email,
)
from app.notifications.recipient_encryption import (
    TransactionalEmailKeyringConfigurationError,
    TransactionalEmailRecipientCipher,
    TransactionalEmailRecipientError,
)
from app.notifications.resend_transport import DEFAULT_MAXIMUM_DEFERRAL, ResendTransport
from app.repositories.account_token_repository import AccountTokenRepository
from app.repositories.transactional_email_delivery_repository import (
    COMPLETION_EVENT_TYPES,
    NONTERMINAL_STATUSES,
    TransactionalEmailDeliveryRepository,
)
from app.services.background_job_worker import (
    BackgroundJobDeferredError,
    BackgroundJobExecutionError,
    BackgroundJobSnapshot,
)
from app.services.workspace_deletion_email import (
    SCOPED_DELETION_EVENTS,
    deletion_reminder_key,
    deletion_requested_key,
    reminder_time,
)
from app.services.workspace_lifecycle import (
    is_active_workspace,
    is_deletion_pending,
    is_resumable_onboarding,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_DISABLED_DEFERRAL = timedelta(minutes=15)
DEFAULT_RATE_LIMIT_WAIT = timedelta(seconds=60)
MAXIMUM_QUOTA_DEFERRAL = timedelta(days=32)

CODE_INVALID_PAYLOAD = "invalid_job_payload"
# A job in one of these states never sends: it was canceled on purpose or already finished.
_JOB_NO_LONGER_ACTIVE = (
    BackgroundJobStatus.CANCELED,
    BackgroundJobStatus.SUCCEEDED,
    BackgroundJobStatus.FAILED,
)

CODE_DELIVERY_MISSING = "delivery_missing"
CODE_OWNERSHIP_MISMATCH = "delivery_ownership_mismatch"
CODE_DELIVERY_NOT_DUE = "delivery_not_due"
CODE_DELIVERY_DISABLED = "delivery_disabled"
CODE_CONTEXT_UNSUPPORTED = "delivery_context_unsupported"
CODE_RECIPIENT_UNAVAILABLE = "recipient_unavailable"
CODE_STATE_UNAVAILABLE = "delivery_state_unavailable"

TOKEN_EVENTS = frozenset(
    {
        TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION,
        TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET,
    }
)
_SAFE_MESSAGES = {
    CODE_INVALID_PAYLOAD: "The transactional-email job payload is invalid.",
    CODE_DELIVERY_MISSING: "The transactional-email delivery is unavailable.",
    CODE_OWNERSHIP_MISMATCH: "The job does not own the transactional-email delivery.",
    CODE_DELIVERY_NOT_DUE: "The transactional-email delivery is not yet due.",
    CODE_DELIVERY_DISABLED: "Transactional email delivery is disabled.",
    CODE_CONTEXT_UNSUPPORTED: "The transactional-email event cannot be delivered.",
    CODE_RECIPIENT_UNAVAILABLE: "The transactional-email recipient is unavailable.",
    CODE_STATE_UNAVAILABLE: "The transactional-email delivery state is unavailable.",
}


def _safe_message(code: str) -> str:
    return _SAFE_MESSAGES.get(code, "The transactional email could not be delivered.")


def _permanent(code: str) -> BackgroundJobExecutionError:
    return BackgroundJobExecutionError(
        safe_error_code=code,
        safe_error_message=_safe_message(code),
        retryable=False,
    )


class _RecipientIneligible(Exception):
    pass


class _RecipientFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _PreparedAttempt:
    """Phase-one result held only in process memory."""

    delivery_id: int
    attempt: int
    event_type: TransactionalEmailEventType
    user_id: int | None
    recipient: str = field(repr=False)
    raw_token: str | None = field(repr=False)
    # Non-secret dates captured under the phase-one lock for deletion notices.
    deletion_fields: dict[str, object] = field(default_factory=dict, repr=False)


def default_transport_factory(settings: Settings) -> TransactionalEmailTransport:
    if settings.transactional_email_api_key is None:
        raise EmailProviderError(EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID)

    return ResendTransport(
        api_key=settings.transactional_email_api_key,
        connect_timeout_seconds=settings.transactional_email_connect_timeout_seconds,
        read_timeout_seconds=settings.transactional_email_read_timeout_seconds,
    )


class TransactionalEmailDeliveryHandler:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        transport_factory: Callable[[Settings], TransactionalEmailTransport] = (
            default_transport_factory
        ),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        maximum_deferral: timedelta = DEFAULT_MAXIMUM_DEFERRAL,
        disabled_deferral: timedelta = DEFAULT_DISABLED_DEFERRAL,
    ) -> None:
        if maximum_deferral <= timedelta(0) or disabled_deferral <= timedelta(0):
            raise ValueError("Deferral windows must be positive.")

        self.session_factory = session_factory
        self.settings = settings
        self.transport_factory = transport_factory
        self.now = now
        self.maximum_deferral = maximum_deferral
        self.disabled_deferral = disabled_deferral

    def __call__(self, job: BackgroundJobSnapshot) -> None:
        try:
            delivery_id = validate_transactional_email_job_payload(job.payload)
        except ValueError:
            raise _permanent(CODE_INVALID_PAYLOAD) from None

        prepared = self._run_phase(lambda session: self._prepare(session, job, delivery_id))

        if prepared is None:
            return

        outcome = self._transmit(prepared)
        attempt = _AttemptRef(prepared.delivery_id, prepared.attempt, prepared.event_type)
        user_id = prepared.user_id
        # Drop the only reference to the plaintext token and recipient.
        del prepared

        self._run_phase(
            lambda session: self._record_outcome(session, job, attempt, user_id, outcome)
        )

    # Phase helpers -------------------------------------------------------

    def _run_phase(self, phase):
        try:
            with self.session_factory() as session:
                return phase(session)
        except BackgroundJobExecutionError, BackgroundJobDeferredError:
            raise
        except SQLAlchemyError:
            # Rolled back. A committed phase-one attempt left ``sending``
            # is reconciled as an uncertain attempt on the next run.
            raise BackgroundJobExecutionError(
                safe_error_code=CODE_STATE_UNAVAILABLE,
                safe_error_message=_safe_message(CODE_STATE_UNAVAILABLE),
                retryable=True,
            ) from None

    def _prepare(
        self,
        session: Session,
        job: BackgroundJobSnapshot,
        delivery_id: int,
    ) -> _PreparedAttempt | None:
        # Lock order is delivery, then (read only) the job: reset completion, suppression
        # and account deletion all lock deliveries before touching jobs, so a plain job
        # read here can never deadlock with them. The delivery lock also holds back an
        # account deletion until this phase commits, so a job read as active stays so.
        deliveries = TransactionalEmailDeliveryRepository(session)
        delivery = deliveries.get(delivery_id, for_update=True)
        row = session.get(BackgroundJob, job.id, populate_existing=True)

        # Authority before any recipient, token or send: an intentionally canceled
        # job (account deletion) or a vanished row is a quiet stale outcome.
        if row is None or row.status in _JOB_NO_LONGER_ACTIVE:
            LOGGER.info("Transactional email job no longer active: job_id=%s.", job.id)
            return None

        if delivery is None:
            if job.workspace_id is not None:
                # A scoped delivery disappears only through user or
                # workspace cascade; nothing remains to send.
                LOGGER.info(
                    "Transactional email delivery removed before sending: job_id=%s.",
                    job.id,
                )
                return None

            # An unscoped delivery is removed on purpose only together with its job
            # (account deletion cancels it first), so an active job with no delivery
            # is an unexpected loss and stays observable.
            raise _permanent(CODE_DELIVERY_MISSING)

        if not job_owns_delivery(job.workspace_id, delivery):
            raise _permanent(CODE_OWNERSHIP_MISMATCH)

        if delivery.status not in NONTERMINAL_STATUSES:
            return None

        now = self.now()

        if delivery.available_at > now:
            raise BackgroundJobDeferredError(
                available_at=delivery.available_at,
                safe_error_code=CODE_DELIVERY_NOT_DUE,
                safe_error_message=_safe_message(CODE_DELIVERY_NOT_DUE),
            )

        # Pre-send gate. A future local quota check belongs here, before
        # any attempt or token is created.
        if not self.settings.transactional_email_enabled:
            raise BackgroundJobDeferredError(
                available_at=now + self.disabled_deferral,
                safe_error_code=CODE_DELIVERY_DISABLED,
                safe_error_message=_safe_message(CODE_DELIVERY_DISABLED),
            )

        if self.settings.transactional_email_provider != "resend":
            self._fail_and_commit(
                session, deliveries, delivery, EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID
            )

        if (
            delivery.status is TransactionalEmailDeliveryStatus.SENDING
            and delivery.attempt_count >= delivery.maximum_attempts
        ):
            # The last attempt's outcome is unknown and none remain.
            self._fail_and_commit(
                session, deliveries, delivery, EmailFailureCategory.PROVIDER_TIMEOUT_UNKNOWN
            )

        try:
            recipient, deletion_fields = self._resolve_recipient(session, delivery, now)
        except _RecipientIneligible:
            deliveries.cancel_ineligible(delivery, now=now)
            session.commit()
            return None
        except _RecipientFailure as failure:
            self._fail_and_commit(session, deliveries, delivery, failure.code)

        attempt = deliveries.begin_attempt(delivery, now=now)
        raw_token = self._issue_token(session, delivery, attempt, now)
        session.commit()

        return _PreparedAttempt(
            delivery_id=delivery.id,
            attempt=attempt,
            event_type=delivery.event_type,
            user_id=delivery.user_id,
            recipient=recipient,
            raw_token=raw_token,
            deletion_fields=deletion_fields,
        )

    def _transmit(
        self, prepared: _PreparedAttempt
    ) -> EmailAcceptance | EmailProviderError | _RenderFailure:
        try:
            rendered = render_transactional_email(
                prepared.event_type,
                public_base_url=self.settings.transactional_email_public_base_url or "",
                fields=self._render_fields(prepared),
            )
        except EmailRenderingError:
            return _RenderFailure()

        sender_address = self.settings.transactional_email_from_address

        if sender_address is None:
            return EmailProviderError(EmailFailureCategory.PROVIDER_CONFIGURATION_INVALID)

        message = OutgoingEmail(
            recipient=prepared.recipient,
            sender_address=sender_address,
            sender_name=self.settings.transactional_email_from_name,
            subject=rendered.subject,
            text_body=rendered.text_body,
            html_body=rendered.html_body,
            idempotency_key=provider_idempotency_key(
                delivery_id=prepared.delivery_id,
                attempt=prepared.attempt,
            ),
        )

        try:
            transport = self.transport_factory(self.settings)
        except EmailProviderError as exc:
            return exc

        try:
            return transport.send(message)
        except EmailProviderError as exc:
            return exc
        except Exception:
            # Never surface an unclassified exception that might carry content.
            return EmailProviderError(EmailFailureCategory.PROVIDER_RESPONSE_INVALID)
        finally:
            close = getattr(transport, "close", None)

            if callable(close):
                close()

    def _record_outcome(
        self,
        session: Session,
        job: BackgroundJobSnapshot,
        attempt: _AttemptRef,
        user_id: int | None,
        outcome: EmailAcceptance | EmailProviderError | _RenderFailure,
    ) -> None:
        deliveries = TransactionalEmailDeliveryRepository(session)
        delivery = deliveries.get(attempt.delivery_id, for_update=True)

        if delivery is None or not deliveries.is_current_attempt(delivery, attempt.attempt):
            # Removed by cascade, canceled, or superseded by a newer attempt
            # after lease recovery. Never overwrite the newer state.
            LOGGER.info(
                "Transactional email outcome no longer current: job_id=%s, attempt=%s.",
                job.id,
                attempt.attempt,
            )
            return

        now = self.now()

        if isinstance(outcome, EmailAcceptance):
            deliveries.mark_sent(
                delivery,
                attempt=attempt.attempt,
                provider_message_id=outcome.provider_message_id,
                now=now,
            )
            self._supersede_tokens(session, attempt, user_id, now)
            session.commit()
            return

        if isinstance(outcome, _RenderFailure):
            deliveries.mark_permanently_failed(
                delivery,
                safe_failure_code=CODE_CONTEXT_UNSUPPORTED,
                attempt=attempt.attempt,
                now=now,
            )
            session.commit()
            raise _permanent(CODE_CONTEXT_UNSUPPORTED)

        code = outcome.category.value
        attempts_remain = delivery.attempt_count < delivery.maximum_attempts

        if outcome.disposition is EmailFailureDisposition.DEFERRABLE and attempts_remain:
            available_at = self._deferral_time(outcome, now)
            deliveries.schedule_retry(
                delivery,
                attempt=attempt.attempt,
                safe_failure_code=code,
                available_at=available_at,
            )
            session.commit()
            raise BackgroundJobDeferredError(
                available_at=available_at,
                safe_error_code=code,
                safe_error_message=_safe_message(code),
            )

        if (
            outcome.disposition is EmailFailureDisposition.RETRYABLE
            and attempts_remain
            and job.attempt_count < job.maximum_attempts
        ):
            deliveries.schedule_retry(
                delivery,
                attempt=attempt.attempt,
                safe_failure_code=code,
                # The job's jittered retry time is authoritative; the delivery
                # is eligible as soon as the job runs again.
                available_at=now,
            )
            session.commit()
            raise BackgroundJobExecutionError(
                safe_error_code=code,
                safe_error_message=_safe_message(code),
                retryable=True,
            )

        deliveries.mark_permanently_failed(
            delivery,
            safe_failure_code=code,
            attempt=attempt.attempt,
            now=now,
        )
        session.commit()
        raise _permanent(code)

    # Supporting operations ----------------------------------------------

    def _fail_and_commit(
        self,
        session: Session,
        deliveries: TransactionalEmailDeliveryRepository,
        delivery: TransactionalEmailDelivery,
        code: str,
    ) -> NoReturn:
        code = str(code)
        deliveries.mark_permanently_failed(delivery, safe_failure_code=code, now=self.now())
        session.commit()
        raise _permanent(code)

    def _resolve_recipient(
        self,
        session: Session,
        delivery: TransactionalEmailDelivery,
        now: datetime,
    ) -> tuple[str, dict[str, object]]:
        """Return the current recipient and any deletion render dates."""

        event = delivery.event_type

        if event in COMPLETION_EVENT_TYPES:
            return self._decrypt_completion_recipient(delivery), {}

        if delivery.scope is TransactionalEmailScope.UNSCOPED and delivery.user_id is not None:
            return self._resolve_personal_recipient(session, delivery), {}

        if delivery.scope is not TransactionalEmailScope.WORKSPACE or delivery.user_id is None:
            raise _RecipientFailure(CODE_CONTEXT_UNSUPPORTED)

        user = session.scalar(
            select(User).where(
                User.id == delivery.user_id,
                User.workspace_id == delivery.workspace_id,
            )
        )

        if (
            user is None
            or user.role is not UserRole.ADMIN
            or not user.is_active
            or not user.normalized_email
        ):
            raise _RecipientIneligible

        workspace = session.get(Installation, delivery.workspace_id)

        if workspace is None:
            raise _RecipientIneligible

        if event in SCOPED_DELETION_EVENTS:
            if user.email_verified_at is None:
                raise _RecipientIneligible

            return user.normalized_email, _deletion_event_fields(delivery, workspace, now)

        if event is TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION and (
            user.email_verified_at is not None
            or workspace.registration_status is not WorkspaceRegistrationStatus.PROVISIONAL
            or workspace.onboarding_stage is not OnboardingStage.EMAIL_VERIFICATION_PENDING
        ):
            raise _RecipientIneligible

        # Mirrors the reset-request route: recovery stays available during
        # resumable onboarding and the FR-20 deletion grace period.
        if event is TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET and (
            user.email_verified_at is None
            or not (
                is_active_workspace(workspace)
                or is_resumable_onboarding(workspace)
                or is_deletion_pending(workspace)
            )
        ):
            raise _RecipientIneligible

        return user.normalized_email, {}

    def _resolve_personal_recipient(
        self,
        session: Session,
        delivery: TransactionalEmailDelivery,
    ) -> str:
        """Current recipient of a workspace-free personal identity delivery.

        Only the delivery's own user is read, and only while that user is
        still a personal account whose state fits the event.
        """

        user = session.get(User, delivery.user_id)

        if (
            user is None
            or classify_identity(user) is not IdentityKind.PERSONAL
            or not user.is_active
            or not user.normalized_email
        ):
            raise _RecipientIneligible

        event = delivery.event_type

        if event is TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION and (
            user.email_verified_at is not None
        ):
            raise _RecipientIneligible

        if event is TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET and (
            user.email_verified_at is None
        ):
            raise _RecipientIneligible

        return user.normalized_email

    def _decrypt_completion_recipient(self, delivery: TransactionalEmailDelivery) -> str:
        if (
            delivery.scope is not TransactionalEmailScope.UNSCOPED
            or delivery.encrypted_recipient is None
            or delivery.recipient_key_version is None
        ):
            raise _RecipientFailure(CODE_RECIPIENT_UNAVAILABLE)

        try:
            cipher = TransactionalEmailRecipientCipher(self.settings.transactional_email_keyring())
            return cipher.decrypt(
                ciphertext=delivery.encrypted_recipient,
                key_version=delivery.recipient_key_version,
                delivery_id=delivery.id,
            )
        except TransactionalEmailRecipientError, TransactionalEmailKeyringConfigurationError:
            raise _RecipientFailure(CODE_RECIPIENT_UNAVAILABLE) from None

    def _issue_token(
        self,
        session: Session,
        delivery: TransactionalEmailDelivery,
        attempt: int,
        now: datetime,
    ) -> str | None:
        if delivery.event_type not in TOKEN_EVENTS or delivery.user_id is None:
            return None

        tokens = AccountTokenRepository(session)
        raw_token = generate_account_token()

        if delivery.event_type is TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION:
            tokens.create_delivery_email_verification_token(
                user_id=delivery.user_id,
                raw_token=raw_token,
                expires_at=now + timedelta(hours=self.settings.email_verification_lifetime_hours),
                delivery_id=delivery.id,
                delivery_attempt=attempt,
                now=now,
            )
        else:
            tokens.create_delivery_password_reset_token(
                user_id=delivery.user_id,
                raw_token=raw_token,
                expires_at=now + timedelta(minutes=self.settings.password_reset_lifetime_minutes),
                delivery_id=delivery.id,
                delivery_attempt=attempt,
                now=now,
            )

        return raw_token

    def _render_fields(self, prepared: _PreparedAttempt) -> dict[str, object]:
        match prepared.event_type:
            case TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION:
                return {
                    "token": prepared.raw_token,
                    "expires_hours": self.settings.email_verification_lifetime_hours,
                }
            case TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET:
                return {
                    "token": prepared.raw_token,
                    "expires_minutes": self.settings.password_reset_lifetime_minutes,
                }
            case _:
                return dict(prepared.deletion_fields)

    def _supersede_tokens(
        self,
        session: Session,
        attempt: _AttemptRef,
        user_id: int | None,
        now: datetime,
    ) -> None:
        if user_id is None or attempt.event_type not in TOKEN_EVENTS:
            return

        tokens = AccountTokenRepository(session)

        if attempt.event_type is TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION:
            tokens.supersede_email_verification_tokens(
                user_id=user_id, accepted_delivery_id=attempt.delivery_id, now=now
            )
        else:
            tokens.supersede_password_reset_tokens(
                user_id=user_id, accepted_delivery_id=attempt.delivery_id, now=now
            )

    def _deferral_time(self, error: EmailProviderError, now: datetime) -> datetime:
        if error.category is EmailFailureCategory.PROVIDER_QUOTA_EXHAUSTED:
            ceiling = MAXIMUM_QUOTA_DEFERRAL
        else:
            ceiling = self.maximum_deferral

        requested = error.retry_not_before or (now + DEFAULT_RATE_LIMIT_WAIT)
        wait = min(max(requested - now, timedelta(seconds=1)), ceiling)

        return now + wait


@dataclass(frozen=True, slots=True)
class _AttemptRef:
    delivery_id: int
    attempt: int
    event_type: TransactionalEmailEventType


class _RenderFailure:
    """Sentinel outcome: the persisted context could not be rendered."""


def _deletion_event_fields(
    delivery: TransactionalEmailDelivery,
    workspace: Installation,
    now: datetime,
) -> dict[str, object]:
    """Recheck a scoped deletion notice against the current lifecycle.

    The delivery's idempotency key embeds its deletion generation, so a
    notice is current only when the key recomputed from the workspace's
    persisted schedule matches. A canceled or replaced request, or a
    deletion already due, makes the notice ineligible (canceled, not sent).
    An early reminder defers to its reminder time without an attempt.
    """

    event = delivery.event_type

    if event is TransactionalEmailEventType.WORKSPACE_DELETION_CANCELED:
        # Valid only while the workspace stayed restored; a later request
        # suppresses this notice in its own transaction.
        if not is_active_workspace(workspace):
            raise _RecipientIneligible

        return {}

    requested_at = workspace.deletion_requested_at
    scheduled_for = workspace.deletion_scheduled_for

    if (
        not is_deletion_pending(workspace)
        or requested_at is None
        or scheduled_for is None
        or now >= scheduled_for
    ):
        raise _RecipientIneligible

    if event is TransactionalEmailEventType.WORKSPACE_DELETION_REQUESTED:
        if delivery.idempotency_key != deletion_requested_key(workspace.id, requested_at):
            raise _RecipientIneligible

        return {
            "requested_on": requested_at.astimezone(UTC).date(),
            "scheduled_for": scheduled_for.astimezone(UTC).date(),
        }

    reminder_at = reminder_time(requested_at=requested_at, scheduled_for=scheduled_for)

    if reminder_at is None or delivery.idempotency_key != deletion_reminder_key(
        workspace.id, scheduled_for
    ):
        raise _RecipientIneligible

    if now < reminder_at:
        raise BackgroundJobDeferredError(
            available_at=reminder_at,
            safe_error_code=CODE_DELIVERY_NOT_DUE,
            safe_error_message=_safe_message(CODE_DELIVERY_NOT_DUE),
        )

    return {"scheduled_for": scheduled_for.astimezone(UTC).date()}


def job_owns_delivery(job_workspace_id: int | None, delivery: TransactionalEmailDelivery) -> bool:
    """A scoped job must match the delivery workspace; an unscoped job only an
    unscoped delivery."""

    if job_workspace_id is None:
        return delivery.workspace_id is None and delivery.scope is TransactionalEmailScope.UNSCOPED

    return (
        delivery.workspace_id == job_workspace_id
        and delivery.scope is TransactionalEmailScope.WORKSPACE
    )
