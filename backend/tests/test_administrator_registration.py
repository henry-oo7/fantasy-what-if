from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.auth_models import (
    EmailVerificationToken,
    User,
    UserRole,
)
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    RegistrationMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation, TransactionalEmailDelivery
from app.db.session import engine
from app.services.administrator_registration import (
    DuplicateAdministratorEmailError,
    InvalidWorkspaceNameError,
    InvalidWorkspaceSlugError,
    RegistrationConflictError,
    RegistrationUnavailableError,
    generate_internal_admin_username,
    normalize_workspace_name,
    normalize_workspace_slug,
    register_provisional_administrator,
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
        "workspace_name": " New Test League ",
        "workspace_slug": f"new-league-{suffix}",
        "administrator_display_name": (" New Administrator "),
        "administrator_email": (f"new-admin-{suffix}@example.com"),
        "administrator_password": ("correct horse battery staple"),
    }


def registration_counts(
    session: Session,
) -> tuple[int, int, int, int, int]:
    """Return (workspaces, users, tokens, deliveries, email jobs)."""

    return (
        int(session.scalar(select(func.count(Installation.id))) or 0),
        int(session.scalar(select(func.count(User.id))) or 0),
        int(session.scalar(select(func.count(EmailVerificationToken.id))) or 0),
        int(session.scalar(select(func.count(TransactionalEmailDelivery.id))) or 0),
        int(session.scalar(select(func.count(BackgroundJob.id))) or 0),
    )


def test_open_registration_creates_atomic_provisional_identity(
    database_session: Session,
) -> None:
    values = unique_registration()
    timestamp = datetime(
        2026,
        11,
        20,
        19,
        0,
        tzinfo=UTC,
    )

    result = register_provisional_administrator(
        database_session,
        registration_mode=RegistrationMode.OPEN,
        now=timestamp,
        **values,
    )

    assert result.workspace.id is not None
    assert result.workspace.application_name == ("New Test League")
    assert result.workspace.slug == (values["workspace_slug"])
    assert result.workspace.normalized_slug == (values["workspace_slug"])
    assert result.workspace.status is (InstallationStatus.SETUP_IN_PROGRESS)
    assert result.workspace.registration_status is (WorkspaceRegistrationStatus.PROVISIONAL)
    assert result.workspace.onboarding_stage is (OnboardingStage.EMAIL_VERIFICATION_PENDING)
    assert result.workspace.setup_started_at == timestamp

    assert result.administrator.id is not None
    assert result.administrator.workspace_id == (result.workspace.id)
    assert result.administrator.role is UserRole.ADMIN
    assert result.administrator.email == (values["administrator_email"])
    assert result.administrator.normalized_email == (values["administrator_email"])
    assert result.administrator.email_verified_at is None
    assert result.administrator.username.startswith("admin-")
    assert values["administrator_email"] not in (result.administrator.username)

    assert not hasattr(result, "verification")

    deliveries = identity_deliveries(database_session, result.administrator.id)

    assert len(deliveries) == 1
    assert deliveries[0].event_type is VERIFICATION
    assert deliveries[0].idempotency_key == registration_verification_key(result.administrator.id)
    assert deliveries[0].workspace_id == result.workspace.id
    assert deliveries[0].user_id == result.administrator.id
    assert len(email_jobs_for(database_session, [deliveries[0].id])) == 1
    assert (
        database_session.scalar(
            select(func.count(EmailVerificationToken.id)).where(
                EmailVerificationToken.user_id == result.administrator.id
            )
        )
        == 0
    )
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
        register_provisional_administrator(
            database_session,
            registration_mode=registration_mode,
            **unique_registration(),
        )

    assert registration_counts(database_session) == before


def test_duplicate_email_leaves_no_partial_registration(
    database_session: Session,
) -> None:
    first = unique_registration()

    register_provisional_administrator(
        database_session,
        registration_mode=RegistrationMode.OPEN,
        **first,
    )

    before = registration_counts(database_session)
    second = unique_registration()
    second["administrator_email"] = first["administrator_email"]

    with pytest.raises(DuplicateAdministratorEmailError):
        register_provisional_administrator(
            database_session,
            registration_mode=RegistrationMode.OPEN,
            **second,
        )

    assert registration_counts(database_session) == before


def test_duplicate_slug_leaves_no_partial_registration(
    database_session: Session,
) -> None:
    first = unique_registration()

    register_provisional_administrator(
        database_session,
        registration_mode=RegistrationMode.OPEN,
        **first,
    )

    before = registration_counts(database_session)
    second = unique_registration()
    second["workspace_slug"] = first["workspace_slug"]

    with pytest.raises(
        RegistrationConflictError,
        match="slug is unavailable",
    ):
        register_provisional_administrator(
            database_session,
            registration_mode=RegistrationMode.OPEN,
            **second,
        )

    assert registration_counts(database_session) == before


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ab",
        "-league",
        "league-",
        "league--name",
        "League Name",
        "league_name",
        "x" * 51,
        "admin",
        "api",
        "login",
        "registration",
        "setup",
    ],
)
def test_invalid_or_reserved_slugs_are_rejected(
    value: str,
) -> None:
    with pytest.raises(InvalidWorkspaceSlugError):
        normalize_workspace_slug(value)


@pytest.mark.parametrize(
    ("provided", "expected"),
    [
        (" Jimmy-And-Friends ", "jimmy-and-friends"),
        ("league2026", "league2026"),
        ("league-2026-east", "league-2026-east"),
    ],
)
def test_workspace_slug_is_normalized(
    provided: str,
    expected: str,
) -> None:
    assert normalize_workspace_slug(provided) == expected


def test_workspace_name_is_normalized() -> None:
    assert normalize_workspace_name("  Jimmy   and   Friends  ") == "Jimmy and Friends"


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "x" * 121,
    ],
)
def test_invalid_workspace_names_are_rejected(
    value: str,
) -> None:
    with pytest.raises(InvalidWorkspaceNameError):
        normalize_workspace_name(value)


def test_internal_admin_username_is_private_compatible_value() -> None:
    first = generate_internal_admin_username()
    second = generate_internal_admin_username()

    assert first.startswith("admin-")
    assert second.startswith("admin-")
    assert first != second
    assert len(first) <= 50
    assert len(second) <= 50
