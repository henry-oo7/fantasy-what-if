"""Service-level tests for personal-account registration (ADR 0010)."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.auth_models import (
    EmailVerificationToken,
    User,
)
from app.db.platform_enums import RegistrationMode, TransactionalEmailScope
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
    TransactionalEmailDelivery,
)
from app.db.session import engine
from app.services.account_registration import (
    DuplicateAccountEmailError,
    InvalidDisplayNameError,
    RegistrationResult,
    RegistrationUnavailableError,
    register_personal_account,
)
from app.services.identity_email import registration_verification_key
from tests.support.identity_email import VERIFICATION, email_jobs_for, identity_deliveries


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


def unique_registration() -> dict[str, str]:
    suffix = uuid4().hex[:12]

    return {
        "display_name": " New Account Holder ",
        "email": f"new-account-{suffix}@example.com",
        "password": "correct horse battery staple",
    }


def registration_counts(session: Session) -> tuple[int, int, int, int, int, int, int]:
    """Return (installations, connections, claims, users, tokens, deliveries, jobs)."""

    return tuple(  # type: ignore[return-value]
        int(session.scalar(select(func.count(model.id))) or 0)
        for model in (
            Installation,
            ProviderConnection,
            ProviderLeagueClaim,
            User,
            EmailVerificationToken,
            TransactionalEmailDelivery,
            BackgroundJob,
        )
    )


def test_open_registration_creates_atomic_personal_account(
    database_session: Session,
) -> None:
    values = unique_registration()
    before = registration_counts(database_session)

    result = register_personal_account(
        database_session,
        registration_mode=RegistrationMode.OPEN,
        **values,
    )

    assert isinstance(result, RegistrationResult)

    user = result.user

    assert user.id is not None
    assert user.display_name == "New Account Holder"
    assert user.email == values["email"]
    assert user.normalized_email == values["email"]
    assert user.email_verified_at is None
    assert user.is_active is True
    assert user.workspace_id is None
    assert user.role is None
    assert user.username is None
    assert values["password"] not in user.password_hash

    assert not hasattr(result, "verification")
    assert not hasattr(result, "workspace")

    deliveries = identity_deliveries(database_session, user.id)

    assert len(deliveries) == 1
    assert deliveries[0].event_type is VERIFICATION
    assert deliveries[0].idempotency_key == registration_verification_key(user.id)
    assert deliveries[0].scope is TransactionalEmailScope.UNSCOPED
    assert deliveries[0].workspace_id is None
    assert deliveries[0].user_id == user.id

    jobs = email_jobs_for(database_session, [deliveries[0].id])

    assert len(jobs) == 1
    assert jobs[0].payload == {"version": 1, "delivery_id": deliveries[0].id}

    # Exactly one user, one delivery, one job; no installation, claim,
    # connection, or token.
    after = registration_counts(database_session)

    assert tuple(a - b for a, b in zip(after, before, strict=True)) == (0, 0, 0, 1, 0, 1, 1)
    assert database_session.in_transaction()


@pytest.mark.parametrize(
    "registration_mode",
    [
        RegistrationMode.CLOSED,
        RegistrationMode.MAINTENANCE,
    ],
)
def test_unavailable_registration_creates_no_state(
    database_session: Session,
    registration_mode: RegistrationMode,
) -> None:
    before = registration_counts(database_session)

    with pytest.raises(
        RegistrationUnavailableError,
        match="unavailable",
    ):
        register_personal_account(
            database_session,
            registration_mode=registration_mode,
            **unique_registration(),
        )

    assert registration_counts(database_session) == before


@pytest.mark.parametrize("display_name", ["", " ", "\t \n"])
def test_blank_display_name_is_rejected_without_state(
    database_session: Session,
    display_name: str,
) -> None:
    values = unique_registration() | {"display_name": display_name}
    before = registration_counts(database_session)

    with pytest.raises(InvalidDisplayNameError):
        register_personal_account(
            database_session,
            registration_mode=RegistrationMode.OPEN,
            **values,
        )

    assert registration_counts(database_session) == before


@pytest.mark.parametrize(
    "transform",
    [str, str.upper, str.title],
    ids=["same-case", "upper-case", "title-case"],
)
def test_duplicate_email_leaves_no_partial_registration(
    database_session: Session,
    transform,
) -> None:
    first = unique_registration()

    register_personal_account(
        database_session,
        registration_mode=RegistrationMode.OPEN,
        **first,
    )

    before = registration_counts(database_session)
    second = unique_registration() | {"email": transform(first["email"])}

    with pytest.raises(DuplicateAccountEmailError):
        register_personal_account(
            database_session,
            registration_mode=RegistrationMode.OPEN,
            **second,
        )

    assert registration_counts(database_session) == before
