"""Personal account deletion (ADR 0010, FR-17).

A confirmed, immediate deletion of one personal account. There is no
recovery state: the 30-day window applies to orphaned canonical League data
(``app.services.orphan_league_purge``), never to the account. This is not
workspace deletion and shares none of its state.

One transaction, caller commits. Lock order:

1. the user row, ``FOR UPDATE``: blocks login, password reset, session
   creation and League import for this user (import locks the user row before
   any League, so no new access row for this user can appear afterwards);
2. every affected canonical League, in ascending ID order.

User-before-League is the order every other flow uses (import, orphan
marking, purge take the user, if any, before a League), so no cycle exists.

Only this user's rows are touched. Canonical League data is never deleted
here: a League with no remaining *active* access and no valid temporary
workspace claim is marked orphaned and left to the retention purge.
``reverification_required`` access never counts as active (ADR 0010), so a
League whose only other users are in that state is orphaned too.

The completion notice is an unscoped delivery with an encrypted recipient
(no user, no workspace owner), created in the same transaction and so
exactly as durable as the deletion itself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth.identity import IdentityKind, classify_identity
from app.auth.security import verify_password
from app.db.auth_models import AuthSession, AuthSessionPurpose, User
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    TransactionalEmailEventType,
)
from app.db.platform_models import BackgroundJob, ProviderConnection, TransactionalEmailDelivery
from app.notifications.recipient_encryption import (
    TransactionalEmailKeyringConfigurationError,
    TransactionalEmailRecipientCipher,
    TransactionalEmailRecipientError,
)
from app.repositories.background_job_repository import QUEUED_JOB_STATUSES
from app.repositories.league_access_repository import LeagueAccessRepository
from app.services.transactional_email_intents import queue_unscoped_completion_email

LOGGER = logging.getLogger(__name__)

ACCOUNT_DELETION_CONFIRMATION = "DELETE MY ACCOUNT"
ACCOUNT_DELETION_REQUEST_ACTION = "account_deletion_request"
ACCOUNT_DELETION_MAXIMUM_ATTEMPTS = 5
ACCOUNT_DELETION_ATTEMPT_WINDOW_MINUTES = 60

RecipientCipherFactory = Callable[[], TransactionalEmailRecipientCipher]


class AccountDeletionError(RuntimeError):
    """Base error for a refused deletion, with a safe code."""

    code = "account_deletion_unavailable"


class AccountDeletionUnavailableError(AccountDeletionError):
    """The session, user or account state does not permit deletion (one outcome)."""


class AccountDeletionPasswordError(AccountDeletionError):
    code = "invalid_current_password"


class AccountDeletionConfirmationError(AccountDeletionError):
    code = "account_deletion_confirmation_mismatch"


class AccountDeletionNoticeUnavailableError(AccountDeletionError):
    """The completion notice could not be created, so nothing was deleted."""

    code = "account_deletion_notice_unavailable"


def confirmation_matches(confirmation: str) -> bool:
    return " ".join(confirmation.split()).upper() == ACCOUNT_DELETION_CONFIRMATION


def _is_deletable_personal_user(user: User | None) -> bool:
    return (
        user is not None
        and classify_identity(user) is IdentityKind.PERSONAL
        and user.is_active
        and user.email_verified_at is not None
        and bool(user.normalized_email)
    )


def lock_personal_user(
    session: Session, *, user_id: int, auth_session_id: int, now: datetime
) -> User:
    """Lock the user row and require an active verified personal user on a live standard session.

    Every refusal is the one AccountDeletionUnavailableError, so nothing about the
    account's state is revealed. Shared with personal League removal.
    """

    user = session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if not _is_deletable_personal_user(user):
        raise AccountDeletionUnavailableError("Account deletion is unavailable.")

    assert user is not None
    auth_session = session.get(AuthSession, auth_session_id)

    if (
        auth_session is None
        or auth_session.user_id != user.id
        or auth_session.purpose is not AuthSessionPurpose.STANDARD
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
    ):
        raise AccountDeletionUnavailableError("Account deletion is unavailable.")

    return user


def _league_ids(session: Session, user_id: int) -> list[int]:
    return list(
        session.scalars(
            select(Season.league_id)
            .join(UserLeagueSeasonAccess, UserLeagueSeasonAccess.season_id == Season.id)
            .where(UserLeagueSeasonAccess.user_id == user_id)
            .distinct()
            .order_by(Season.league_id)
        )
    )


def _lock_leagues(session: Session, league_ids: list[int]) -> None:
    if league_ids:
        session.execute(
            select(League.id).where(League.id.in_(league_ids)).order_by(League.id).with_for_update()
        )


def _cancel_jobs(
    session: Session,
    *conditions: object,
    statuses: tuple[BackgroundJobStatus, ...] = QUEUED_JOB_STATUSES,
    now: datetime,
) -> None:
    session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.status.in_(statuses), *conditions)
        .values(
            status=BackgroundJobStatus.CANCELED,
            canceled_at=now,
            completed_at=None,
            failed_at=None,
            claimed_at=None,
            lease_expires_at=None,
            claimed_by=None,
            started_at=None,
            retryable=None,
        )
        .execution_options(synchronize_session=False)
    )


def delete_personal_account(
    session: Session,
    *,
    user_id: int,
    auth_session_id: int,
    current_password: str,
    confirmation: str,
    recipient_cipher_factory: RecipientCipherFactory,
    now: datetime | None = None,
) -> None:
    """Delete the personal account and its personal graph; the caller commits."""

    current_time = now or datetime.now(UTC)

    user = lock_personal_user(
        session, user_id=user_id, auth_session_id=auth_session_id, now=current_time
    )

    if not verify_password(current_password, user.password_hash):
        raise AccountDeletionPasswordError("The current password is incorrect.")

    if not confirmation_matches(confirmation):
        raise AccountDeletionConfirmationError("The confirmation does not match.")

    recipient = user.normalized_email
    assert recipient is not None

    affected = _league_ids(session, user.id)
    _lock_leagues(session, affected)

    # Pending identity mail and reverification of this user never run again.
    # Deliveries are locked before their jobs, the order every other email flow uses.
    delivery_ids = select(TransactionalEmailDelivery.id).where(
        TransactionalEmailDelivery.user_id == user.id
    )
    session.execute(
        select(TransactionalEmailDelivery.id)
        .where(TransactionalEmailDelivery.user_id == user.id)
        .order_by(TransactionalEmailDelivery.id)
        .with_for_update()
    )
    # A running identity job is canceled too, before its delivery row goes: the worker
    # rechecks job authority under a lock, so it can no longer send or persist.
    _cancel_jobs(
        session,
        BackgroundJob.job_type == BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY,
        BackgroundJob.payload["delivery_id"].as_integer().in_(delivery_ids),
        statuses=(*QUEUED_JOB_STATUSES, BackgroundJobStatus.RUNNING),
        now=current_time,
    )
    _cancel_jobs(
        session,
        BackgroundJob.job_type == BackgroundJobType.ACCESS_REVERIFICATION,
        BackgroundJob.payload["user_id"].as_integer() == user.id,
        now=current_time,
    )

    # Access rows reference connections with RESTRICT, so they go first. A
    # Season never keeps a pointer to a credential that is about to be deleted.
    own_connections = select(ProviderConnection.id).where(ProviderConnection.user_id == user.id)
    session.execute(
        update(Season)
        .where(Season.provider_connection_id.in_(own_connections))
        .values(provider_connection_id=None)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        delete(UserLeagueSeasonAccess)
        .where(UserLeagueSeasonAccess.user_id == user.id)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        delete(ProviderConnection)
        .where(ProviderConnection.user_id == user.id)
        .execution_options(synchronize_session=False)
    )

    # Orphan decision under the League locks, after the access removal.
    leagues = LeagueAccessRepository(session)

    for league_id in affected:
        leagues.mark_league_orphaned(league_id=league_id, orphaned_at=current_time)

    try:
        queue_unscoped_completion_email(
            session,
            recipient=recipient,
            cipher=recipient_cipher_factory(),
            idempotency_key=f"identity-email:account_deletion_completed:{uuid4().hex}",
            event_type=TransactionalEmailEventType.ACCOUNT_DELETION_COMPLETED,
            available_at=current_time,
        )
    except TransactionalEmailKeyringConfigurationError, TransactionalEmailRecipientError:
        raise AccountDeletionNoticeUnavailableError(
            "The completion notice is unavailable."
        ) from None

    # Cascades: sessions, verification and reset tokens, preferences, and the
    # user's own identity deliveries. Nothing else references the user.
    session.execute(
        delete(User).where(User.id == user.id).execution_options(synchronize_session=False)
    )
    session.flush()

    LOGGER.info("Personal account deleted: leagues_affected=%s.", len(affected))
