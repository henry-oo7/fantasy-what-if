"""Shared helpers for transactional-email persistence tests."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.auth_models import User, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    TransactionalEmailDeliveryStatus,
    TransactionalEmailEventType,
    TransactionalEmailScope,
)
from app.db.platform_models import Installation, TransactionalEmailDelivery
from app.db.session import engine
from app.notifications.recipient_encryption import (
    TransactionalEmailKeyring,
    TransactionalEmailRecipientCipher,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)
RECIPIENT = "completed-owner@synthetic.invalid"


def database_session_fixture() -> Iterator[Session]:
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


def make_workspace(session: Session) -> Installation:
    slug = f"email-{uuid4().hex[:12]}"
    workspace = Installation(
        application_name="Email Schema",
        slug=slug,
        normalized_slug=slug,
        status=InstallationStatus.ACTIVE,
    )
    session.add(workspace)
    session.flush()
    return workspace


def make_user(session: Session, workspace: Installation) -> User:
    user = User(
        username=f"u-{uuid4().hex[:12]}",
        display_name="Email Schema",
        workspace_id=workspace.id,
        email=None,
        password_hash="$argon2id$synthetic",
        role=UserRole.SPECTATOR,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def make_cipher(version: int = 1) -> TransactionalEmailRecipientCipher:
    return TransactionalEmailRecipientCipher(
        TransactionalEmailKeyring(
            keys={version: Fernet.generate_key().decode("ascii")},
            current_version=version,
        )
    )


def scoped_delivery(
    workspace_id: int, user_id: int | None, **fields: Any
) -> TransactionalEmailDelivery:
    values: dict[str, Any] = {
        "scope": TransactionalEmailScope.WORKSPACE,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "event_type": TransactionalEmailEventType.ADMINISTRATOR_EMAIL_VERIFICATION,
        "idempotency_key": f"test:{uuid4().hex}",
    }
    values.update(fields)
    return TransactionalEmailDelivery(**values)


def unscoped_delivery(**fields: Any) -> TransactionalEmailDelivery:
    values: dict[str, Any] = {
        "scope": TransactionalEmailScope.UNSCOPED,
        "workspace_id": None,
        "user_id": None,
        "event_type": TransactionalEmailEventType.WORKSPACE_DELETION_COMPLETED,
        "idempotency_key": f"test:{uuid4().hex}",
        "encrypted_recipient": "ciphertext-placeholder",
        "recipient_key_version": 1,
    }
    values.update(fields)
    return TransactionalEmailDelivery(**values)


def terminal_fields(
    status: TransactionalEmailDeliveryStatus,
    *,
    at: datetime = NOW,
) -> dict[str, Any]:
    """Valid timestamp fields for a final status."""

    retention = {"retention_expires_at": at + timedelta(days=90)}

    if status is TransactionalEmailDeliveryStatus.SENT:
        return {"status": status, "sent_at": at, **retention}

    if status is TransactionalEmailDeliveryStatus.PERMANENTLY_FAILED:
        return {
            "status": status,
            "failed_at": at,
            "safe_failure_code": "provider_rejected",
            **retention,
        }

    return {"status": status, "canceled_at": at, **retention}


def assert_rejected(session: Session, instance: object) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(instance)
        session.flush()


def assert_accepted(session: Session, instance: object) -> None:
    with session.begin_nested():
        session.add(instance)
        session.flush()
