from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hmac_identifier
from app.db.auth_models import LoginAttempt
from app.db.session import engine
from app.repositories.login_attempt_repository import (
    LoginAttemptRepository,
)

NOW = datetime(
    2026,
    11,
    10,
    18,
    0,
    tzinfo=UTC,
)
SECRET = "test-login-throttle-secret-32-characters"


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def repository(
    session: Session,
) -> LoginAttemptRepository:
    return LoginAttemptRepository(
        session,
        secret=SECRET,
        maximum_attempts=3,
        attempt_window_minutes=15,
        lockout_minutes=15,
    )


def test_first_and_second_failures_are_not_locked(
    database_session: Session,
) -> None:
    throttle = repository(database_session)

    first = throttle.record_failure(
        username="Admin",
        source_address="203.0.113.10",
        now=NOW,
    )
    second = throttle.record_failure(
        username="admin",
        source_address="203.0.113.10",
        now=NOW + timedelta(seconds=5),
    )

    assert first.failed_attempts == 1
    assert first.is_locked is False
    assert second.failed_attempts == 2
    assert second.is_locked is False


def test_third_failure_locks_for_fifteen_minutes(
    database_session: Session,
) -> None:
    throttle = repository(database_session)

    for offset in range(2):
        throttle.record_failure(
            username="admin",
            source_address="203.0.113.10",
            now=NOW + timedelta(seconds=offset),
        )

    state = throttle.record_failure(
        username="admin",
        source_address="203.0.113.10",
        now=NOW + timedelta(seconds=2),
    )

    assert state.failed_attempts == 3
    assert state.is_locked is True
    assert state.retry_after_seconds == 900


def test_active_lockout_is_returned_before_password_check(
    database_session: Session,
) -> None:
    throttle = repository(database_session)

    for offset in range(3):
        throttle.record_failure(
            username="admin",
            source_address="203.0.113.10",
            now=NOW + timedelta(seconds=offset),
        )

    state = throttle.throttle_state(
        username="admin",
        source_address="203.0.113.10",
        now=NOW + timedelta(minutes=5),
    )

    assert state.is_locked is True
    assert state.retry_after_seconds == 602


def test_expired_window_resets_attempt_count(
    database_session: Session,
) -> None:
    throttle = repository(database_session)

    throttle.record_failure(
        username="admin",
        source_address="203.0.113.10",
        now=NOW,
    )

    state = throttle.record_failure(
        username="admin",
        source_address="203.0.113.10",
        now=NOW + timedelta(minutes=16),
    )

    assert state.failed_attempts == 1
    assert state.is_locked is False


def test_success_clear_removes_matching_state(
    database_session: Session,
) -> None:
    throttle = repository(database_session)

    throttle.record_failure(
        username="admin",
        source_address="203.0.113.10",
        now=NOW,
    )

    throttle.clear(
        username="admin",
        source_address="203.0.113.10",
    )

    username_hash = hmac_identifier(
        "admin",
        secret=SECRET,
        namespace="login-username",
    )
    source_hash = hmac_identifier(
        "203.0.113.10",
        secret=SECRET,
        namespace="login-source",
    )

    assert (
        database_session.scalar(
            select(LoginAttempt).where(
                LoginAttempt.username_hash == username_hash,
                LoginAttempt.source_hash == source_hash,
            )
        )
        is None
    )


def test_different_sources_have_independent_counters(
    database_session: Session,
) -> None:
    throttle = repository(database_session)

    first = throttle.record_failure(
        username="admin",
        source_address="203.0.113.10",
        now=NOW,
    )
    second = throttle.record_failure(
        username="admin",
        source_address="203.0.113.11",
        now=NOW,
    )

    assert first.failed_attempts == 1
    assert second.failed_attempts == 1

    username_hash = hmac_identifier(
        "admin",
        secret=SECRET,
        namespace="login-username",
    )

    rows = database_session.scalars(
        select(LoginAttempt).where(
            LoginAttempt.username_hash == username_hash,
        )
    ).all()

    assert len(rows) == 2


def test_raw_username_and_ip_are_not_stored(
    database_session: Session,
) -> None:
    throttle = repository(database_session)

    throttle.record_failure(
        username="sensitive-admin",
        source_address="203.0.113.10",
        now=NOW,
    )

    username_hash = hmac_identifier(
        "sensitive-admin",
        secret=SECRET,
        namespace="login-username",
    )
    source_hash = hmac_identifier(
        "203.0.113.10",
        secret=SECRET,
        namespace="login-source",
    )

    attempt = database_session.scalar(
        select(LoginAttempt).where(
            LoginAttempt.username_hash == username_hash,
            LoginAttempt.source_hash == source_hash,
        )
    )

    assert attempt is not None
    assert attempt.username_hash != "sensitive-admin"
    assert attempt.source_hash != "203.0.113.10"
    assert len(attempt.username_hash) == 64
    assert len(attempt.source_hash) == 64


def test_progressive_delay_schedule(
    database_session: Session,
) -> None:
    throttle = LoginAttemptRepository(
        database_session,
        secret=SECRET,
        maximum_attempts=5,
        attempt_window_minutes=15,
        lockout_minutes=15,
        delay_schedule_seconds=(
            (3, 15),
            (4, 60),
            (5, 900),
        ),
    )

    first = throttle.record_failure(
        username="progressive-admin",
        source_address="account-wide",
        now=NOW,
    )
    second = throttle.record_failure(
        username="progressive-admin",
        source_address="account-wide",
        now=NOW + timedelta(seconds=1),
    )
    third = throttle.record_failure(
        username="progressive-admin",
        source_address="account-wide",
        now=NOW + timedelta(seconds=2),
    )

    assert first.retry_after_seconds == 0
    assert second.retry_after_seconds == 0
    assert third.retry_after_seconds == 15


def test_progressive_delay_increases_after_expiry(
    database_session: Session,
) -> None:
    throttle = LoginAttemptRepository(
        database_session,
        secret=SECRET,
        maximum_attempts=5,
        attempt_window_minutes=15,
        lockout_minutes=15,
        delay_schedule_seconds=(
            (3, 15),
            (4, 60),
            (5, 900),
        ),
    )

    for offset in range(3):
        throttle.record_failure(
            username="progressive-admin",
            source_address="account-wide",
            now=NOW + timedelta(seconds=offset),
        )

    fourth = throttle.record_failure(
        username="progressive-admin",
        source_address="account-wide",
        now=NOW + timedelta(seconds=18),
    )

    assert fourth.failed_attempts == 4
    assert fourth.retry_after_seconds == 60


def test_client_progressive_schedule(
    database_session: Session,
) -> None:
    throttle = LoginAttemptRepository(
        database_session,
        secret=SECRET,
        maximum_attempts=5,
        attempt_window_minutes=15,
        lockout_minutes=15,
        delay_schedule_seconds=(
            (3, 30),
            (4, 120),
            (5, 900),
        ),
    )

    for offset in range(2):
        state = throttle.record_failure(
            username="spectator-client",
            source_address="browser-network-bucket",
            now=NOW + timedelta(seconds=offset),
        )

        assert state.retry_after_seconds == 0

    third = throttle.record_failure(
        username="spectator-client",
        source_address="browser-network-bucket",
        now=NOW + timedelta(seconds=2),
    )

    assert third.failed_attempts == 3
    assert third.retry_after_seconds == 30


def test_precomputed_policy_bucket_tracks_failures(
    database_session: Session,
) -> None:
    throttle = LoginAttemptRepository(
        database_session,
        secret=SECRET,
        maximum_attempts=5,
        delay_schedule_seconds=(
            (3, 30),
            (4, 120),
            (5, 900),
        ),
    )

    bucket_username = hmac_identifier(
        "spectator",
        secret=SECRET,
        namespace="client-login-username",
    )
    bucket_source = hmac_identifier(
        "browser-network",
        secret=SECRET,
        namespace="login-client-bucket",
    )

    first = throttle.record_failure_for_bucket(
        bucket_username=bucket_username,
        bucket_source=bucket_source,
        now=NOW,
    )
    second = throttle.record_failure_for_bucket(
        bucket_username=bucket_username,
        bucket_source=bucket_source,
        now=NOW + timedelta(seconds=1),
    )
    third = throttle.record_failure_for_bucket(
        bucket_username=bucket_username,
        bucket_source=bucket_source,
        now=NOW + timedelta(seconds=2),
    )

    assert first.failed_attempts == 1
    assert second.failed_attempts == 2
    assert third.failed_attempts == 3
    assert third.retry_after_seconds == 30

    state = throttle.throttle_state_for_bucket(
        bucket_username=bucket_username,
        bucket_source=bucket_source,
        now=NOW + timedelta(seconds=10),
    )

    assert state.is_locked is True
    assert state.retry_after_seconds == 22


def test_precomputed_policy_bucket_can_be_cleared(
    database_session: Session,
) -> None:
    throttle = LoginAttemptRepository(
        database_session,
        secret=SECRET,
    )

    bucket_username = hmac_identifier(
        "administrator",
        secret=SECRET,
        namespace="admin-login-account",
    )
    bucket_source = "account-wide"

    throttle.record_failure_for_bucket(
        bucket_username=bucket_username,
        bucket_source=bucket_source,
        now=NOW,
    )

    throttle.clear_bucket(
        bucket_username=bucket_username,
        bucket_source=bucket_source,
    )

    stored = database_session.scalar(
        select(LoginAttempt).where(
            LoginAttempt.username_hash == bucket_username,
            LoginAttempt.source_hash == bucket_source,
        )
    )

    assert stored is None


@pytest.mark.parametrize(
    (
        "bucket_username",
        "bucket_source",
    ),
    [
        ("", "source"),
        ("username", ""),
        ("   ", "source"),
        ("username", "   "),
    ],
)
def test_precomputed_policy_bucket_rejects_empty_values(
    database_session: Session,
    bucket_username: str,
    bucket_source: str,
) -> None:
    throttle = repository(database_session)

    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        throttle.throttle_state_for_bucket(
            bucket_username=bucket_username,
            bucket_source=bucket_source,
            now=NOW,
        )


def test_network_spray_bucket_counts_across_usernames(
    database_session: Session,
) -> None:
    from app.auth.login_throttle_policy import (
        resolve_source_spray_policy,
    )

    policy = resolve_source_spray_policy(
        source_address="203.0.113.117",
        secret=SECRET,
    )

    throttle = LoginAttemptRepository(
        database_session,
        secret=SECRET,
        maximum_attempts=20,
        attempt_window_minutes=15,
        lockout_minutes=30,
        delay_schedule_seconds=(policy.delay_schedule_seconds),
    )

    for index in range(19):
        state = throttle.record_failure_for_bucket(
            bucket_username=(policy.bucket_username),
            bucket_source=(policy.bucket_source),
            now=NOW + timedelta(seconds=index),
        )

        assert state.is_locked is False

    twentieth = throttle.record_failure_for_bucket(
        bucket_username=(policy.bucket_username),
        bucket_source=(policy.bucket_source),
        now=NOW + timedelta(seconds=19),
    )

    assert twentieth.failed_attempts == 20
    assert twentieth.is_locked is True
    assert twentieth.retry_after_seconds == 1800


def test_network_spray_bucket_survives_client_bucket_clear(
    database_session: Session,
) -> None:
    from app.auth.login_throttle_policy import (
        resolve_source_spray_policy,
    )

    source_policy = resolve_source_spray_policy(
        source_address="203.0.113.117",
        secret=SECRET,
    )

    throttle = LoginAttemptRepository(
        database_session,
        secret=SECRET,
        maximum_attempts=20,
        attempt_window_minutes=15,
        lockout_minutes=30,
        delay_schedule_seconds=(source_policy.delay_schedule_seconds),
    )

    throttle.record_failure_for_bucket(
        bucket_username=(source_policy.bucket_username),
        bucket_source=source_policy.bucket_source,
        now=NOW,
    )

    throttle.clear_bucket(
        bucket_username=hmac_identifier(
            "spectator",
            secret=SECRET,
            namespace="client-login-username",
        ),
        bucket_source=hmac_identifier(
            "browser-network",
            secret=SECRET,
            namespace="login-client-bucket",
        ),
    )

    source_state = throttle.throttle_state_for_bucket(
        bucket_username=(source_policy.bucket_username),
        bucket_source=(source_policy.bucket_source),
        now=NOW + timedelta(seconds=1),
    )

    assert source_state.failed_attempts == 1
    assert source_state.is_locked is False
