"""Persistence primitives for durable transactional-email deliveries.

BackgroundJob remains the queue and lease authority; nothing here claims,
leases, or sends. No plaintext recipient, token, link, or body is handled
beyond the one-way encryption call for the unscoped completion notice.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
)
from app.db.platform_models import TransactionalEmailDelivery
from app.notifications.recipient_encryption import TransactionalEmailRecipientCipher

IDEMPOTENCY_CONSTRAINT = "uq_transactional_email_deliveries_idempotency_key"
SAFE_STATE_RETENTION = timedelta(days=90)
PROVIDER_MESSAGE_ID_RETENTION = timedelta(days=30)
_SAFE_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ATTEMPTABLE_STATUSES = frozenset(
    {
        TransactionalEmailDeliveryStatus.PENDING,
        TransactionalEmailDeliveryStatus.RETRY_SCHEDULED,
        # A stale ``sending`` row is an attempt with an unknown outcome
        # (worker crash or lease loss); it is retried as a new attempt.
        TransactionalEmailDeliveryStatus.SENDING,
    }
)

IDENTITY_EVENT_TYPES = frozenset(
    {
        TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION,
        TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET,
        TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_CHANGED,
    }
)
NONTERMINAL_STATUSES = frozenset(
    {
        TransactionalEmailDeliveryStatus.PENDING,
        TransactionalEmailDeliveryStatus.SENDING,
        TransactionalEmailDeliveryStatus.RETRY_SCHEDULED,
    }
)
CANCELABLE_STATUSES = frozenset(
    {
        TransactionalEmailDeliveryStatus.PENDING,
        TransactionalEmailDeliveryStatus.RETRY_SCHEDULED,
    }
)


class TransactionalEmailDeliveryError(RuntimeError):
    """Base error for delivery persistence."""


class DeliveryIdempotencyConflictError(TransactionalEmailDeliveryError):
    """One idempotency key was reused for a different delivery intent."""


class DeliveryStateError(TransactionalEmailDeliveryError):
    """The delivery is not in a state that allows the operation."""


@dataclass(frozen=True, slots=True)
class CreateDeliveryResult:
    delivery: TransactionalEmailDelivery
    existing: bool


@dataclass(frozen=True, slots=True)
class DeliveryTerminalState:
    """Recipient-free terminal state, safe to expose to operators."""

    status: TransactionalEmailDeliveryStatus
    final_at: datetime
    safe_failure_code: str | None


def _require_safe_failure_code(code: str) -> str:
    if not isinstance(code, str) or not _SAFE_FAILURE_CODE.fullmatch(code):
        raise ValueError("The safe failure code is invalid.")

    return code


def normalize_delivery_idempotency_key(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("The delivery idempotency key must be a string.")

    normalized = key.strip()

    if not normalized or len(normalized) > 255:
        raise ValueError("The delivery idempotency key must be 1 to 255 characters.")

    return normalized


class TransactionalEmailDeliveryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self,
        delivery_id: int,
        *,
        for_update: bool = False,
    ) -> TransactionalEmailDelivery | None:
        statement = select(TransactionalEmailDelivery).where(
            TransactionalEmailDelivery.id == delivery_id
        )

        if for_update:
            statement = statement.with_for_update()

        return self.session.scalar(statement)

    def get_by_idempotency_key(self, key: str) -> TransactionalEmailDelivery | None:
        return self.session.scalar(
            select(TransactionalEmailDelivery).where(
                TransactionalEmailDelivery.idempotency_key
                == normalize_delivery_idempotency_key(key)
            )
        )

    def create_workspace_delivery(
        self,
        *,
        workspace_id: int,
        user_id: int | None,
        event_type: TransactionalEmailEventType,
        idempotency_key: str,
        available_at: datetime | None = None,
        maximum_attempts: int = 5,
    ) -> CreateDeliveryResult:
        """Create or return a workspace-scoped delivery intent.

        The recipient is resolved from the active user at send time and is
        never stored here.
        """

        if workspace_id < 1 or (user_id is not None and user_id < 1):
            raise ValueError("workspace_id and user_id must be positive.")

        if event_type is TransactionalEmailEventType.WORKSPACE_DELETION_COMPLETED:
            raise ValueError("The completion notice must be created as an unscoped delivery.")

        if event_type in IDENTITY_EVENT_TYPES and user_id is None:
            raise ValueError("Identity email events require user_id.")

        key = normalize_delivery_idempotency_key(idempotency_key)

        def build() -> TransactionalEmailDelivery:
            return TransactionalEmailDelivery(
                scope=TransactionalEmailScope.WORKSPACE,
                workspace_id=workspace_id,
                user_id=user_id,
                event_type=event_type,
                idempotency_key=key,
                status=TransactionalEmailDeliveryStatus.PENDING,
                available_at=available_at or datetime.now(UTC),
                maximum_attempts=maximum_attempts,
            )

        return self._create_or_get(
            key,
            build,
            scope=TransactionalEmailScope.WORKSPACE,
            event_type=event_type,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    def create_unscoped_completion_delivery(
        self,
        *,
        recipient: str,
        cipher: TransactionalEmailRecipientCipher,
        idempotency_key: str,
        available_at: datetime | None = None,
        maximum_attempts: int = 5,
    ) -> CreateDeliveryResult:
        """Create or return the unscoped deletion-completion delivery.

        The recipient is encrypted, bound to the delivery ID, before insert.
        """

        key = normalize_delivery_idempotency_key(idempotency_key)

        existing = self.get_by_idempotency_key(key)

        if existing is not None:
            return self._resolve_existing(
                existing,
                scope=TransactionalEmailScope.UNSCOPED,
                event_type=TransactionalEmailEventType.WORKSPACE_DELETION_COMPLETED,
                workspace_id=None,
                user_id=None,
            )

        delivery_id = self._allocate_id()
        encrypted = cipher.encrypt(recipient=recipient, delivery_id=delivery_id)

        def build() -> TransactionalEmailDelivery:
            return TransactionalEmailDelivery(
                id=delivery_id,
                scope=TransactionalEmailScope.UNSCOPED,
                workspace_id=None,
                user_id=None,
                event_type=TransactionalEmailEventType.WORKSPACE_DELETION_COMPLETED,
                idempotency_key=key,
                status=TransactionalEmailDeliveryStatus.PENDING,
                available_at=available_at or datetime.now(UTC),
                maximum_attempts=maximum_attempts,
                encrypted_recipient=encrypted.ciphertext,
                recipient_key_version=encrypted.key_version,
            )

        return self._create_or_get(
            key,
            build,
            scope=TransactionalEmailScope.UNSCOPED,
            event_type=TransactionalEmailEventType.WORKSPACE_DELETION_COMPLETED,
            workspace_id=None,
            user_id=None,
        )

    def cancel_pending(
        self,
        delivery: TransactionalEmailDelivery,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Cancel a not-yet-sending delivery and scrub recipient material.

        Returns False when it was already canceled. A sending, sent, or
        failed delivery cannot be canceled here.
        """

        if delivery.status is TransactionalEmailDeliveryStatus.CANCELED:
            return False

        if delivery.status not in CANCELABLE_STATUSES:
            raise DeliveryStateError("Only pending or retry-scheduled deliveries may be canceled.")

        timestamp = now or datetime.now(UTC)

        delivery.status = TransactionalEmailDeliveryStatus.CANCELED
        delivery.canceled_at = timestamp
        delivery.retention_expires_at = timestamp + SAFE_STATE_RETENTION
        delivery.safe_failure_code = None
        delivery.encrypted_recipient = None
        delivery.recipient_key_version = None
        self.session.flush()

        return True

    def begin_attempt(
        self,
        delivery: TransactionalEmailDelivery,
        *,
        now: datetime | None = None,
    ) -> int:
        """Persist a new provider attempt and mark the delivery ``sending``.

        The caller holds the row lock and commits before transmission, so
        the returned attempt number (the provider-attempt identity) is
        durable before any provider call.
        """

        if delivery.status not in ATTEMPTABLE_STATUSES:
            raise DeliveryStateError("Only a nonterminal delivery may begin an attempt.")

        if delivery.attempt_count >= delivery.maximum_attempts:
            raise DeliveryStateError("The delivery has no remaining attempts.")

        delivery.attempt_count += 1
        delivery.status = TransactionalEmailDeliveryStatus.SENDING
        delivery.last_attempt_at = now or datetime.now(UTC)
        delivery.safe_failure_code = None
        self.session.flush()

        return delivery.attempt_count

    def mark_sent(
        self,
        delivery: TransactionalEmailDelivery,
        *,
        attempt: int,
        provider_message_id: str,
        now: datetime | None = None,
    ) -> None:
        """Record provider acceptance of the current attempt and scrub."""

        self._require_current_attempt(delivery, attempt)
        message_id = provider_message_id.strip()

        if not message_id or len(message_id) > 255:
            raise ValueError("The provider message ID is invalid.")

        timestamp = now or datetime.now(UTC)

        delivery.status = TransactionalEmailDeliveryStatus.SENT
        delivery.sent_at = timestamp
        delivery.provider_message_id = message_id
        delivery.provider_message_id_expires_at = timestamp + PROVIDER_MESSAGE_ID_RETENTION
        delivery.retention_expires_at = timestamp + SAFE_STATE_RETENTION
        delivery.safe_failure_code = None
        delivery.encrypted_recipient = None
        delivery.recipient_key_version = None
        self.session.flush()

    def schedule_retry(
        self,
        delivery: TransactionalEmailDelivery,
        *,
        attempt: int,
        safe_failure_code: str,
        available_at: datetime,
    ) -> None:
        """Return the current attempt's delivery to ``retry_scheduled``.

        Encrypted completion recipients are preserved for the next attempt.
        """

        self._require_current_attempt(delivery, attempt)

        if delivery.attempt_count >= delivery.maximum_attempts:
            raise DeliveryStateError("The delivery has no remaining attempts.")

        delivery.status = TransactionalEmailDeliveryStatus.RETRY_SCHEDULED
        delivery.safe_failure_code = _require_safe_failure_code(safe_failure_code)
        delivery.available_at = available_at
        self.session.flush()

    def mark_permanently_failed(
        self,
        delivery: TransactionalEmailDelivery,
        *,
        safe_failure_code: str,
        attempt: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Finalize a nonterminal delivery as failed and scrub recipient material.

        ``attempt`` fences provider outcomes; pre-transmission failures pass None.
        """

        if attempt is not None:
            self._require_current_attempt(delivery, attempt)
        elif delivery.status not in NONTERMINAL_STATUSES:
            raise DeliveryStateError("Only a nonterminal delivery may fail.")

        timestamp = now or datetime.now(UTC)

        delivery.status = TransactionalEmailDeliveryStatus.PERMANENTLY_FAILED
        delivery.failed_at = timestamp
        delivery.retention_expires_at = timestamp + SAFE_STATE_RETENTION
        delivery.safe_failure_code = _require_safe_failure_code(safe_failure_code)
        delivery.encrypted_recipient = None
        delivery.recipient_key_version = None
        self.session.flush()

    def cancel_ineligible(
        self,
        delivery: TransactionalEmailDelivery,
        *,
        now: datetime | None = None,
    ) -> None:
        """Cancel a delivery whose recipient is no longer eligible, and scrub."""

        self.cancel_nonterminal(delivery, now=now)

    def cancel_nonterminal(
        self,
        delivery: TransactionalEmailDelivery,
        *,
        now: datetime | None = None,
    ) -> None:
        """Cancel any nonterminal delivery, including one that is ``sending``.

        The caller holds the row lock. A worker whose attempt is still in
        flight observes the cancellation as a non-current attempt and never
        records its outcome.
        """

        if delivery.status not in NONTERMINAL_STATUSES:
            raise DeliveryStateError("Only a nonterminal delivery may be canceled.")

        timestamp = now or datetime.now(UTC)

        delivery.status = TransactionalEmailDeliveryStatus.CANCELED
        delivery.canceled_at = timestamp
        delivery.retention_expires_at = timestamp + SAFE_STATE_RETENTION
        delivery.safe_failure_code = None
        delivery.encrypted_recipient = None
        delivery.recipient_key_version = None
        self.session.flush()

    @staticmethod
    def is_current_attempt(delivery: TransactionalEmailDelivery, attempt: int) -> bool:
        return (
            delivery.status is TransactionalEmailDeliveryStatus.SENDING
            and delivery.attempt_count == attempt
        )

    def _require_current_attempt(self, delivery: TransactionalEmailDelivery, attempt: int) -> None:
        if not self.is_current_attempt(delivery, attempt):
            raise DeliveryStateError("The attempt is no longer current for this delivery.")

    def scrub_recipient(self, delivery: TransactionalEmailDelivery) -> None:
        """Remove encrypted recipient material from a finished delivery."""

        if delivery.status in NONTERMINAL_STATUSES:
            raise DeliveryStateError("Recipient material is needed until the delivery is final.")

        delivery.encrypted_recipient = None
        delivery.recipient_key_version = None
        self.session.flush()

    def get_terminal_state(self, delivery_id: int) -> DeliveryTerminalState | None:
        delivery = self.get(delivery_id)

        if delivery is None or delivery.status in NONTERMINAL_STATUSES:
            return None

        final_at = delivery.sent_at or delivery.failed_at or delivery.canceled_at

        if final_at is None:
            return None

        return DeliveryTerminalState(
            status=delivery.status,
            final_at=final_at,
            safe_failure_code=delivery.safe_failure_code,
        )

    def _allocate_id(self) -> int:
        delivery_id = self.session.scalar(
            select(
                func.nextval(
                    func.pg_get_serial_sequence(TransactionalEmailDelivery.__tablename__, "id")
                )
            )
        )

        if not isinstance(delivery_id, int):
            raise TransactionalEmailDeliveryError("A delivery ID could not be allocated.")

        return delivery_id

    def _create_or_get(
        self,
        key: str,
        build: Callable[[], TransactionalEmailDelivery],
        *,
        scope: TransactionalEmailScope,
        event_type: TransactionalEmailEventType,
        workspace_id: int | None,
        user_id: int | None,
    ) -> CreateDeliveryResult:
        existing = self.get_by_idempotency_key(key)

        if existing is not None:
            return self._resolve_existing(
                existing,
                scope=scope,
                event_type=event_type,
                workspace_id=workspace_id,
                user_id=user_id,
            )

        delivery = build()

        try:
            with self.session.begin_nested():
                self.session.add(delivery)
                self.session.flush()
        except IntegrityError as exc:
            if IDEMPOTENCY_CONSTRAINT not in str(exc.orig):
                raise

            existing = self.get_by_idempotency_key(key)

            if existing is None:
                raise TransactionalEmailDeliveryError(
                    "The idempotent delivery could not be resolved after a conflict."
                ) from exc

            return self._resolve_existing(
                existing,
                scope=scope,
                event_type=event_type,
                workspace_id=workspace_id,
                user_id=user_id,
            )

        return CreateDeliveryResult(delivery=delivery, existing=False)

    @staticmethod
    def _resolve_existing(
        existing: TransactionalEmailDelivery,
        *,
        scope: TransactionalEmailScope,
        event_type: TransactionalEmailEventType,
        workspace_id: int | None,
        user_id: int | None,
    ) -> CreateDeliveryResult:
        if (
            existing.scope is not scope
            or existing.event_type is not event_type
            or existing.workspace_id != workspace_id
            or existing.user_id != user_id
        ):
            raise DeliveryIdempotencyConflictError(
                "The idempotency key belongs to a different delivery intent."
            )

        return CreateDeliveryResult(delivery=existing, existing=True)
