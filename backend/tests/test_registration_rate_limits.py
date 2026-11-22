from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.auth_models import RegistrationRateLimit
from app.db.session import engine
from app.repositories.registration_rate_limit_repository import (
    RegistrationRateLimitRepository,
    _normalize_action,
)


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()

        if transaction.is_active:
            transaction.rollback()

        connection.close()


def repository(
    session: Session,
) -> RegistrationRateLimitRepository:
    return RegistrationRateLimitRepository(
        session,
        secret="registration-rate-limit-test-secret",
    )


def test_first_three_verification_resends_are_allowed(
    database_session: Session,
) -> None:
    identity = f"admin-{uuid4().hex}@example.com"
    now = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)
    results = [
        repository(database_session).record_request(
            action="verification_resend",
            identity=identity,
            maximum_requests=3,
            window_minutes=60,
            now=now + timedelta(minutes=index),
        )
        for index in range(3)
    ]

    assert all(result.allowed for result in results)
    assert [result.request_count for result in results] == [
        1,
        2,
        3,
    ]
    assert all(result.retry_after_seconds == 0 for result in results)


def test_fourth_verification_resend_is_limited(
    database_session: Session,
) -> None:
    identity = f"admin-{uuid4().hex}@example.com"
    now = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)
    limiter = repository(database_session)

    for index in range(3):
        result = limiter.record_request(
            action="verification_resend",
            identity=identity,
            maximum_requests=3,
            window_minutes=60,
            now=now + timedelta(minutes=index),
        )

        assert result.allowed is True

    blocked = limiter.record_request(
        action="verification_resend",
        identity=identity,
        maximum_requests=3,
        window_minutes=60,
        now=now + timedelta(minutes=3),
    )

    assert blocked.allowed is False
    assert blocked.request_count == 4
    assert 1 <= blocked.retry_after_seconds <= 60 * 60


def test_window_resets_after_one_hour(
    database_session: Session,
) -> None:
    identity = f"admin-{uuid4().hex}@example.com"
    now = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)
    limiter = repository(database_session)

    for _ in range(4):
        limiter.record_request(
            action="verification_resend",
            identity=identity,
            maximum_requests=3,
            window_minutes=60,
            now=now,
        )

    reset = limiter.record_request(
        action="verification_resend",
        identity=identity,
        maximum_requests=3,
        window_minutes=60,
        now=now + timedelta(hours=1),
    )

    assert reset.allowed is True
    assert reset.request_count == 1
    assert reset.retry_after_seconds == 0


def test_distinct_actions_have_independent_counters(
    database_session: Session,
) -> None:
    identity = f"admin-{uuid4().hex}@example.com"
    limiter = repository(database_session)

    resend = limiter.record_request(
        action="verification_resend",
        identity=identity,
        maximum_requests=3,
        window_minutes=60,
    )
    reset = limiter.record_request(
        action="password_reset_request",
        identity=identity,
        maximum_requests=3,
        window_minutes=60,
    )

    assert resend.request_count == 1
    assert reset.request_count == 1


def test_raw_identity_is_not_stored(
    database_session: Session,
) -> None:
    identity = f"private-{uuid4().hex}@example.com"

    repository(database_session).record_request(
        action="verification_resend",
        identity=identity,
        maximum_requests=3,
        window_minutes=60,
    )

    stored = database_session.scalar(select(RegistrationRateLimit))

    assert stored is not None
    assert stored.identity_hash != identity
    assert len(stored.identity_hash) == 64
    assert identity not in stored.identity_hash


def test_expired_rate_limits_can_be_deleted(
    database_session: Session,
) -> None:
    identity = f"admin-{uuid4().hex}@example.com"
    now = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)
    request_time = now - timedelta(hours=2)
    limiter = repository(database_session)

    limiter.record_request(
        action="verification_resend",
        identity=identity,
        maximum_requests=3,
        window_minutes=60,
        now=request_time,
    )

    stored = database_session.scalar(
        select(RegistrationRateLimit).where(
            RegistrationRateLimit.action == "verification_resend",
            RegistrationRateLimit.window_started_at == request_time,
            RegistrationRateLimit.last_request_at == request_time,
        )
    )

    assert stored is not None

    stored_id = stored.id

    deleted = limiter.delete_expired(
        retention_minutes=60,
        now=now,
    )

    assert deleted >= 1
    assert (
        database_session.get(
            RegistrationRateLimit,
            stored_id,
        )
        is None
    )


@pytest.mark.parametrize(
    ("provided", "expected"),
    [
        ("verification_resend", "verification_resend"),
        (" Verification-Resend ", "verification_resend"),
        ("password_reset_request", "password_reset_request"),
    ],
)
def test_action_is_normalized(
    provided: str,
    expected: str,
) -> None:
    assert _normalize_action(provided) == expected


@pytest.mark.parametrize(
    "provided",
    [
        "",
        " ",
        "verification resend",
        "verification/resend",
        "x" * 51,
    ],
)
def test_invalid_actions_are_rejected(
    provided: str,
) -> None:
    with pytest.raises(ValueError):
        _normalize_action(provided)
