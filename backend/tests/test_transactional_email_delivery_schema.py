"""Schema and constraint tests for transactional_email_deliveries (ADR 0009)."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
)
from app.db.platform_models import TransactionalEmailDelivery
from app.db.session import engine
from tests.support.transactional_email import (
    NOW,
    assert_accepted,
    assert_rejected,
    database_session_fixture,
    make_user,
    make_workspace,
    scoped_delivery,
    terminal_fields,
    unscoped_delivery,
)

Status = TransactionalEmailDeliveryStatus
Event = TransactionalEmailEventType


@pytest.fixture
def database_session() -> Iterator[Session]:
    yield from database_session_fixture()


@pytest.fixture
def workspace_and_user(database_session: Session):
    workspace = make_workspace(database_session)
    return workspace, make_user(database_session, workspace)


def test_enum_values_are_exact() -> None:
    assert [m.value for m in TransactionalEmailScope] == ["workspace", "unscoped"]
    assert [m.value for m in TransactionalEmailEventType] == [
        "administrator_email_verification",
        "administrator_password_reset",
        "administrator_password_changed",
        "workspace_deletion_requested",
        "workspace_deletion_reminder",
        "workspace_deletion_completed",
        "workspace_deletion_canceled",
        "account_deletion_completed",
    ]
    assert [m.value for m in TransactionalEmailDeliveryStatus] == [
        "pending",
        "sending",
        "retry_scheduled",
        "sent",
        "permanently_failed",
        "canceled",
    ]


def test_database_enums_match_python_enums(database_session: Session) -> None:
    rows = database_session.execute(
        text(
            "SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder) "
            "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
            "WHERE t.typname LIKE 'transactional_email_%' GROUP BY t.typname"
        )
    ).all()

    assert dict(rows) == {
        "transactional_email_scope": [m.value for m in TransactionalEmailScope],
        "transactional_email_event_type": [m.value for m in TransactionalEmailEventType],
        "transactional_email_delivery_status": [m.value for m in TransactionalEmailDeliveryStatus],
    }


def test_no_plaintext_or_secret_columns() -> None:
    columns = {
        column["name"] for column in inspect(engine).get_columns("transactional_email_deliveries")
    }

    assert {"encrypted_recipient", "recipient_key_version"} <= columns
    assert not columns & {
        "recipient_email",
        "recipient",
        "email",
        "token",
        "token_hash",
        "link",
        "subject",
        "body",
        "payload",
    }


def test_expected_indexes_and_no_recipient_index() -> None:
    inspector = inspect(engine)
    indexes = {
        i["name"]: i
        for i in inspector.get_indexes("transactional_email_deliveries")
        if not i["unique"]  # the idempotency unique constraint's own index
    }

    assert set(indexes) == {
        "ix_transactional_email_deliveries_workspace_id",
        "ix_transactional_email_deliveries_user_id",
        "ix_transactional_email_deliveries_status_available_at",
        "ix_transactional_email_deliveries_retention_expires_at",
    }
    assert not any("recipient" in " ".join(i["column_names"]) for i in indexes.values())
    uniques = inspector.get_unique_constraints("transactional_email_deliveries")
    assert [u["column_names"] for u in uniques] == [["idempotency_key"]]


def test_foreign_keys_cascade() -> None:
    foreign_keys = {
        fk["constrained_columns"][0]: fk
        for fk in inspect(engine).get_foreign_keys("transactional_email_deliveries")
    }

    assert foreign_keys["workspace_id"]["referred_table"] == "installations"
    assert foreign_keys["workspace_id"]["options"]["ondelete"] == "CASCADE"
    assert foreign_keys["user_id"]["referred_table"] == "users"
    assert foreign_keys["user_id"]["options"]["ondelete"] == "CASCADE"


def test_valid_scoped_deliveries_for_every_scoped_event(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user

    for event in Event:
        if event in (Event.WORKSPACE_DELETION_COMPLETED, Event.ACCOUNT_DELETION_COMPLETED):
            continue

        assert_accepted(
            database_session,
            scoped_delivery(workspace.id, user.id, event_type=event),
        )


@pytest.mark.parametrize(
    "event",
    [
        Event.WORKSPACE_DELETION_REQUESTED,
        Event.WORKSPACE_DELETION_REMINDER,
        Event.WORKSPACE_DELETION_CANCELED,
    ],
)
def test_deletion_events_may_resolve_administrator_through_workspace(
    database_session: Session, workspace_and_user, event: Event
) -> None:
    workspace, _ = workspace_and_user
    assert_accepted(database_session, scoped_delivery(workspace.id, None, event_type=event))


@pytest.mark.parametrize(
    "event",
    [
        Event.ADMINISTRATOR_EMAIL_VERIFICATION,
        Event.ADMINISTRATOR_PASSWORD_RESET,
        Event.ADMINISTRATOR_PASSWORD_CHANGED,
    ],
)
def test_identity_events_require_user(
    database_session: Session, workspace_and_user, event: Event
) -> None:
    workspace, _ = workspace_and_user
    assert_rejected(database_session, scoped_delivery(workspace.id, None, event_type=event))


def test_scoped_delivery_rejections(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user

    assert_rejected(database_session, scoped_delivery(None, user.id))
    assert_rejected(
        database_session,
        scoped_delivery(workspace.id, user.id, encrypted_recipient="x", recipient_key_version=1),
    )
    assert_rejected(
        database_session,
        scoped_delivery(workspace.id, user.id, event_type=Event.WORKSPACE_DELETION_COMPLETED),
    )


def test_valid_unscoped_completion_delivery(database_session: Session) -> None:
    assert_accepted(database_session, unscoped_delivery())


def test_unscoped_delivery_rejections(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user

    assert_rejected(
        database_session,
        unscoped_delivery(encrypted_recipient=None, recipient_key_version=None),
    )
    assert_rejected(
        database_session, unscoped_delivery(event_type=Event.ADMINISTRATOR_PASSWORD_RESET)
    )
    assert_rejected(database_session, unscoped_delivery(workspace_id=workspace.id))
    assert_rejected(database_session, unscoped_delivery(user_id=user.id))


@pytest.mark.parametrize(
    "fields",
    [
        {"encrypted_recipient": "x", "recipient_key_version": None},
        {"encrypted_recipient": None, "recipient_key_version": 1},
        {"encrypted_recipient": "x", "recipient_key_version": 0},
    ],
)
def test_recipient_ciphertext_and_version_must_pair(
    database_session: Session, fields: dict
) -> None:
    assert_rejected(database_session, unscoped_delivery(**fields))


@pytest.mark.parametrize("status", [Status.SENT, Status.PERMANENTLY_FAILED, Status.CANCELED])
def test_final_unscoped_delivery_must_have_no_recipient(
    database_session: Session, status: Status
) -> None:
    fields = terminal_fields(status)

    assert_rejected(database_session, unscoped_delivery(**fields))
    assert_accepted(
        database_session,
        unscoped_delivery(encrypted_recipient=None, recipient_key_version=None, **fields),
    )


def test_user_deletion_cascades_to_scoped_delivery(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user
    delivery = scoped_delivery(workspace.id, user.id)
    assert_accepted(database_session, delivery)
    delivery_id = delivery.id

    database_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    database_session.expire_all()

    assert database_session.get(TransactionalEmailDelivery, delivery_id) is None


def test_workspace_deletion_cascades_scoped_but_unscoped_completion_survives(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user
    scoped = scoped_delivery(workspace.id, user.id)
    unscoped = unscoped_delivery()
    assert_accepted(database_session, scoped)
    assert_accepted(database_session, unscoped)
    scoped_id, unscoped_id = scoped.id, unscoped.id

    database_session.execute(
        text("DELETE FROM users WHERE workspace_id = :id"), {"id": workspace.id}
    )
    database_session.execute(text("DELETE FROM installations WHERE id = :id"), {"id": workspace.id})
    database_session.expire_all()

    assert database_session.get(TransactionalEmailDelivery, scoped_id) is None
    survivor = database_session.get(TransactionalEmailDelivery, unscoped_id)
    assert survivor is not None
    assert survivor.workspace_id is None and survivor.user_id is None


def test_idempotency_key_is_unique_and_clean(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user
    assert_accepted(database_session, scoped_delivery(workspace.id, user.id, idempotency_key="k1"))

    assert_rejected(database_session, scoped_delivery(workspace.id, user.id, idempotency_key="k1"))
    assert_rejected(database_session, scoped_delivery(workspace.id, user.id, idempotency_key=" k2"))
    assert_rejected(database_session, scoped_delivery(workspace.id, user.id, idempotency_key=""))


def test_repeated_legitimate_events_are_allowed(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user

    for index in range(3):
        assert_accepted(
            database_session,
            scoped_delivery(
                workspace.id,
                user.id,
                event_type=Event.ADMINISTRATOR_PASSWORD_RESET,
                idempotency_key=f"reset:{index}",
            ),
        )

    count = database_session.scalar(
        select(text("count(*)"))
        .select_from(TransactionalEmailDelivery)
        .where(TransactionalEmailDelivery.workspace_id == workspace.id)
    )
    assert count == 3


def test_status_defaults_and_nonnegative_attempts(
    database_session: Session, workspace_and_user
) -> None:
    workspace, user = workspace_and_user
    delivery = scoped_delivery(workspace.id, user.id)
    assert_accepted(database_session, delivery)

    assert delivery.status is Status.PENDING
    assert delivery.attempt_count == 0 and delivery.maximum_attempts == 5
    assert delivery.created_at is not None and delivery.updated_at is not None

    assert_rejected(database_session, scoped_delivery(workspace.id, user.id, attempt_count=-1))
    assert_rejected(database_session, scoped_delivery(workspace.id, user.id, maximum_attempts=0))
    assert_rejected(
        database_session,
        scoped_delivery(workspace.id, user.id, attempt_count=6, maximum_attempts=5),
    )


@pytest.mark.parametrize("status", [Status.PENDING, Status.SENDING, Status.RETRY_SCHEDULED])
def test_nonterminal_states_cannot_pretend_to_be_final(
    database_session: Session, workspace_and_user, status: Status
) -> None:
    workspace, user = workspace_and_user

    assert_accepted(database_session, scoped_delivery(workspace.id, user.id, status=status))

    for field in ("sent_at", "failed_at", "canceled_at", "retention_expires_at"):
        assert_rejected(
            database_session,
            scoped_delivery(workspace.id, user.id, status=status, **{field: NOW}),
        )


def test_final_state_timestamp_combinations(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user

    for status in (Status.SENT, Status.PERMANENTLY_FAILED, Status.CANCELED):
        valid = terminal_fields(status)
        assert_accepted(database_session, scoped_delivery(workspace.id, user.id, **valid))

        # Missing the defining timestamp.
        missing = {
            k: v for k, v in valid.items() if k not in ("sent_at", "failed_at", "canceled_at")
        }
        assert_rejected(database_session, scoped_delivery(workspace.id, user.id, **missing))

        # Missing retention.
        no_retention = {k: v for k, v in valid.items() if k != "retention_expires_at"}
        assert_rejected(database_session, scoped_delivery(workspace.id, user.id, **no_retention))

        # Retention not after the final timestamp.
        assert_rejected(
            database_session,
            scoped_delivery(
                workspace.id, user.id, **{**valid, "retention_expires_at": NOW - timedelta(days=1)}
            ),
        )

    # Contradictory timestamps.
    assert_rejected(
        database_session,
        scoped_delivery(workspace.id, user.id, **terminal_fields(Status.SENT), failed_at=NOW),
    )
    assert_rejected(
        database_session,
        scoped_delivery(workspace.id, user.id, **terminal_fields(Status.CANCELED), sent_at=NOW),
    )


def test_provider_message_id_state(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user
    sent = terminal_fields(Status.SENT)
    expires = NOW + timedelta(days=30)

    assert_accepted(
        database_session,
        scoped_delivery(
            workspace.id,
            user.id,
            **sent,
            provider_message_id="msg_1",
            provider_message_id_expires_at=expires,
        ),
    )
    # Both or neither.
    assert_rejected(
        database_session, scoped_delivery(workspace.id, user.id, **sent, provider_message_id="m")
    )
    assert_rejected(
        database_session,
        scoped_delivery(workspace.id, user.id, **sent, provider_message_id_expires_at=expires),
    )
    # Expiration must follow sent_at.
    assert_rejected(
        database_session,
        scoped_delivery(
            workspace.id,
            user.id,
            **sent,
            provider_message_id="m",
            provider_message_id_expires_at=NOW,
        ),
    )
    # Only a sent delivery has a provider message ID.
    assert_rejected(
        database_session,
        scoped_delivery(
            workspace.id,
            user.id,
            provider_message_id="m",
            provider_message_id_expires_at=expires,
        ),
    )
    # Scrubbed after the 30-day window while the row stays sent.
    assert_accepted(database_session, scoped_delivery(workspace.id, user.id, **sent))


def test_safe_failure_code_state(database_session: Session, workspace_and_user) -> None:
    workspace, user = workspace_and_user

    assert_accepted(
        database_session,
        scoped_delivery(
            workspace.id, user.id, status=Status.RETRY_SCHEDULED, safe_failure_code="timeout"
        ),
    )
    assert_rejected(
        database_session, scoped_delivery(workspace.id, user.id, safe_failure_code="timeout")
    )
    assert_rejected(
        database_session,
        scoped_delivery(
            workspace.id, user.id, **{**terminal_fields(Status.SENT), "safe_failure_code": "x"}
        ),
    )
    # A permanent failure must say why, using a closed, bounded code shape.
    failed = terminal_fields(Status.PERMANENTLY_FAILED)
    assert_rejected(
        database_session,
        scoped_delivery(workspace.id, user.id, **{**failed, "safe_failure_code": None}),
    )
    for bad in ("Has Space", "user@example.invalid", "1leading", ""):
        assert_rejected(
            database_session,
            scoped_delivery(workspace.id, user.id, **{**failed, "safe_failure_code": bad}),
        )
