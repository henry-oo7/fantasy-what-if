"""Delivery-linked verification and reset tokens (ADR 0009, migration a3c9e5f1b7d2)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import generate_account_token, hash_token
from app.db.auth_models import EmailVerificationToken, PasswordResetToken, User
from app.db.platform_enums import TransactionalEmailEventType
from app.db.platform_models import TransactionalEmailDelivery
from app.repositories.account_token_repository import AccountTokenRepository
from app.services.email_verification import verify_email
from app.services.password_recovery import reset_password
from tests.support.email_delivery import make_admin, make_workspace
from tests.support.transactional_email import (
    assert_rejected,
    database_session_fixture,
    scoped_delivery,
)

NOW = datetime.now(UTC)
LATER = NOW + timedelta(hours=1)
VERIFY = TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION
RESET = TransactionalEmailEventType.ADMINISTRATOR_PASSWORD_RESET


@pytest.fixture
def database_session() -> Iterator[Session]:
    yield from database_session_fixture()


def delivery(session: Session, user: User, event=VERIFY) -> TransactionalEmailDelivery:
    row = scoped_delivery(user.workspace_id, user.id, event_type=event)
    session.add(row)
    session.flush()
    return row


def linked_verification(
    session: Session, user: User, delivery_id: int, attempt: int
) -> tuple[str, EmailVerificationToken]:
    raw = generate_account_token()
    token = AccountTokenRepository(session).create_delivery_email_verification_token(
        user_id=user.id,
        raw_token=raw,
        expires_at=LATER,
        delivery_id=delivery_id,
        delivery_attempt=attempt,
        now=NOW,
    )
    return raw, token


def linked_reset(
    session: Session, user: User, delivery_id: int, attempt: int
) -> tuple[str, PasswordResetToken]:
    raw = generate_account_token()
    token = AccountTokenRepository(session).create_delivery_password_reset_token(
        user_id=user.id,
        raw_token=raw,
        expires_at=LATER,
        delivery_id=delivery_id,
        delivery_attempt=attempt,
        now=NOW,
    )
    return raw, token


def legacy_verification(session: Session, user: User) -> EmailVerificationToken:
    return AccountTokenRepository(session).create_email_verification_token(
        user_id=user.id, raw_token=generate_account_token(), expires_at=LATER, now=NOW
    )


def is_valid(session: Session, token) -> bool:
    session.refresh(token)
    return token.used_at is None


def test_linked_tokens_store_only_hash_with_delivery_and_attempt(
    database_session: Session,
) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace, verified=True)
    verify_delivery = delivery(database_session, user)
    reset_delivery = delivery(database_session, user, RESET)

    raw_v, verification = linked_verification(database_session, user, verify_delivery.id, 1)
    raw_r, reset = linked_reset(database_session, user, reset_delivery.id, 3)

    assert verification.token_hash == hash_token(raw_v) != raw_v
    assert (verification.transactional_email_delivery_id, verification.delivery_attempt) == (
        verify_delivery.id,
        1,
    )
    assert reset.token_hash == hash_token(raw_r) != raw_r
    assert (reset.transactional_email_delivery_id, reset.delivery_attempt) == (
        reset_delivery.id,
        3,
    )


def test_worker_token_creation_does_not_invalidate_existing_tokens(
    database_session: Session,
) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace)
    legacy = legacy_verification(database_session, user)
    row = delivery(database_session, user)

    _raw1, first = linked_verification(database_session, user, row.id, 1)
    _raw2, second = linked_verification(database_session, user, row.id, 2)

    assert is_valid(database_session, legacy)
    assert is_valid(database_session, first)
    assert is_valid(database_session, second)


@pytest.mark.parametrize("model", [EmailVerificationToken, PasswordResetToken])
def test_partial_delivery_attempt_pair_is_rejected(database_session: Session, model) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace)
    row = delivery(database_session, user)

    for linked_id, attempt in ((row.id, None), (None, 1), (row.id, 0)):
        assert_rejected(
            database_session,
            model(
                user_id=user.id,
                token_hash=hash_token(generate_account_token()),
                expires_at=LATER,
                transactional_email_delivery_id=linked_id,
                delivery_attempt=attempt,
            ),
        )

    with pytest.raises(ValueError):
        linked_verification(database_session, user, row.id, 0)


def test_delivery_deletion_cascades_linked_tokens_only(database_session: Session) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace, verified=True)
    legacy = legacy_verification(database_session, user)
    row = delivery(database_session, user)
    _raw, linked = linked_verification(database_session, user, row.id, 1)
    reset_row = delivery(database_session, user, RESET)
    _raw_r, linked_reset_token = linked_reset(database_session, user, reset_row.id, 1)
    linked_ids = (linked.id, linked_reset_token.id)

    database_session.delete(row)
    database_session.delete(reset_row)
    database_session.flush()
    database_session.expire_all()

    assert database_session.get(EmailVerificationToken, linked_ids[0]) is None
    assert database_session.get(PasswordResetToken, linked_ids[1]) is None
    assert database_session.get(EmailVerificationToken, legacy.id) is not None


def test_user_deletion_removes_tokens_and_deliveries(database_session: Session) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace)
    legacy = legacy_verification(database_session, user)
    row = delivery(database_session, user)
    _raw, linked = linked_verification(database_session, user, row.id, 1)
    ids = (legacy.id, linked.id, row.id)

    database_session.delete(user)
    database_session.flush()
    database_session.expire_all()

    assert database_session.get(EmailVerificationToken, ids[0]) is None
    assert database_session.get(EmailVerificationToken, ids[1]) is None
    assert database_session.get(TransactionalEmailDelivery, ids[2]) is None


def test_accepting_a_delivery_preserves_all_its_attempts_and_newer_deliveries(
    database_session: Session,
) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace)
    legacy = legacy_verification(database_session, user)
    older = delivery(database_session, user)
    accepted = delivery(database_session, user)
    newer = delivery(database_session, user)
    assert older.id < accepted.id < newer.id

    _r0, older_token = linked_verification(database_session, user, older.id, 1)
    _r1, attempt_one = linked_verification(database_session, user, accepted.id, 1)
    _r2, attempt_two = linked_verification(database_session, user, accepted.id, 2)
    _r3, newer_token = linked_verification(database_session, user, newer.id, 1)

    invalidated = AccountTokenRepository(database_session).supersede_email_verification_tokens(
        user_id=user.id, accepted_delivery_id=accepted.id, now=NOW
    )

    assert invalidated == 2
    assert not is_valid(database_session, legacy)
    assert not is_valid(database_session, older_token)
    assert is_valid(database_session, attempt_one)
    assert is_valid(database_session, attempt_two)
    assert is_valid(database_session, newer_token)


def test_accepting_an_older_delivery_never_invalidates_a_newer_in_flight_delivery(
    database_session: Session,
) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace, verified=True)
    older = delivery(database_session, user, RESET)
    newer = delivery(database_session, user, RESET)
    _r1, older_token = linked_reset(database_session, user, older.id, 1)
    _r2, newer_token = linked_reset(database_session, user, newer.id, 1)

    AccountTokenRepository(database_session).supersede_password_reset_tokens(
        user_id=user.id, accepted_delivery_id=older.id, now=NOW
    )

    assert is_valid(database_session, older_token)
    assert is_valid(database_session, newer_token)


def test_supersession_is_scoped_by_user_and_token_type(database_session: Session) -> None:
    workspace = make_workspace(database_session)
    other_workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace, verified=True)
    other = make_admin(database_session, other_workspace, verified=True)
    other_legacy = legacy_verification(database_session, other)
    reset_row = delivery(database_session, user, RESET)
    _r, reset_token = linked_reset(database_session, user, reset_row.id, 1)
    accepted = delivery(database_session, user)

    AccountTokenRepository(database_session).supersede_email_verification_tokens(
        user_id=user.id, accepted_delivery_id=accepted.id, now=NOW
    )

    assert is_valid(database_session, other_legacy)
    assert is_valid(database_session, reset_token)


def test_consuming_one_verification_token_invalidates_all_remaining(
    database_session: Session,
) -> None:
    workspace = make_workspace(database_session, provisional=True)
    user = make_admin(database_session, workspace)
    legacy = legacy_verification(database_session, user)
    row = delivery(database_session, user)
    raw_one, first = linked_verification(database_session, user, row.id, 1)
    _raw_two, second = linked_verification(database_session, user, row.id, 2)

    verify_email(database_session, raw_token=raw_one)

    assert not is_valid(database_session, legacy)
    assert not is_valid(database_session, first)
    assert not is_valid(database_session, second)
    database_session.refresh(user)
    assert user.email_verified_at is not None


def test_consuming_one_reset_token_invalidates_all_remaining(database_session: Session) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace, verified=True)
    first_row = delivery(database_session, user, RESET)
    second_row = delivery(database_session, user, RESET)
    _raw_one, first = linked_reset(database_session, user, first_row.id, 1)
    raw_two, second = linked_reset(database_session, user, second_row.id, 1)

    reset_password(database_session, raw_token=raw_two, new_password="Synthetic-New-Passphrase-42!")

    assert not is_valid(database_session, first)
    assert not is_valid(database_session, second)


def test_no_plaintext_token_is_stored(database_session: Session) -> None:
    workspace = make_workspace(database_session)
    user = make_admin(database_session, workspace)
    row = delivery(database_session, user)
    raw, _token = linked_verification(database_session, user, row.id, 1)

    stored = database_session.scalars(
        select(EmailVerificationToken.token_hash).where(EmailVerificationToken.user_id == user.id)
    ).all()

    assert raw not in stored
    assert raw not in repr(row.__dict__)
