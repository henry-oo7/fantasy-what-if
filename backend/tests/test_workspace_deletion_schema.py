"""Schema foundation for FR-20 workspace deletion (revision c4e8a1f7d3b9)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, ForeignKey, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.auth_models import (
    AuthSession,
    AuthSessionPurpose,
    SessionRevocationReason,
    User,
    UserRole,
)
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    WorkspaceDeletionOutcome,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation, WorkspaceDeletionRecord
from app.db.session import engine

NOW = datetime(2030, 1, 1, tzinfo=UTC)
LATER = NOW + timedelta(days=7)

FORBIDDEN_COLUMNS = {
    "slug",
    "normalized_slug",
    "email",
    "normalized_email",
    "display_name",
    "user_id",
    "username",
    "ip_address",
    "password",
    "password_hash",
    "token",
    "token_hash",
    "encrypted_credentials",
    "credential_version",
    "credential_schema_version",
    "payload",
    "error",
    "traceback",
}


@pytest.fixture
def database_session() -> Iterator[Session]:
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


def make_workspace(**fields: Any) -> Installation:
    slug = f"deletion-schema-{uuid4().hex[:10]}"
    values: dict[str, Any] = {
        "application_name": "Deletion Schema",
        "slug": slug,
        "normalized_slug": slug,
        "status": InstallationStatus.ACTIVE,
    }
    values.update(fields)
    return Installation(**values)


def make_record(**fields: Any) -> WorkspaceDeletionRecord:
    values: dict[str, Any] = {
        "former_workspace_id": 123,
        "provider": FantasyProvider.ESPN,
        "normalized_provider_league_id": "424242",
        "requested_at": NOW,
        "scheduled_for": LATER,
        "completed_at": LATER + timedelta(minutes=5),
        "outcome": WorkspaceDeletionOutcome.COMPLETED,
        "retention_expires_at": LATER + timedelta(days=90),
    }
    values.update(fields)
    return WorkspaceDeletionRecord(**values)


def assert_rejected(session: Session, instance: object) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(instance)
        session.flush()


def test_enum_values_are_exact() -> None:
    assert [member.value for member in AuthSessionPurpose] == ["standard", "deletion_recovery"]
    assert [member.value for member in WorkspaceDeletionOutcome] == ["completed"]
    assert [member.value for member in SessionRevocationReason] == [
        "replaced",
        "logout",
        "password_reset",
        "spectator_password_reset",
        "spectator_disabled",
        "workspace_deletion_requested",
    ]


def test_installation_deletion_columns_are_nullable_timestamptz() -> None:
    columns = {column["name"]: column for column in inspect(engine).get_columns("installations")}

    for name in ("deletion_requested_at", "deletion_scheduled_for"):
        assert columns[name]["nullable"] is True
        assert columns[name]["type"].timezone is True
        assert getattr(Installation, name).type.timezone is True

    assert Installation.__table__.c.registration_status.nullable is True


def test_installation_lifecycle_constraint_and_partial_index_exist() -> None:
    constraints = {
        constraint.name
        for constraint in Installation.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_installations_deletion_lifecycle" in constraints

    inspector = inspect(engine)
    database_checks = {c["name"] for c in inspector.get_check_constraints("installations")}
    assert "ck_installations_deletion_lifecycle" in database_checks

    index = next(
        i
        for i in inspector.get_indexes("installations")
        if i["name"] == "ix_installations_deletion_scheduled_for_pending"
    )
    assert index["column_names"] == ["deletion_scheduled_for"]
    assert "deletion_pending" in index["dialect_options"]["postgresql_where"]


def test_null_registration_status_without_timestamps_is_valid(
    database_session: Session,
) -> None:
    database_session.add(make_workspace(registration_status=None))
    database_session.flush()


@pytest.mark.parametrize(
    "status",
    [
        WorkspaceRegistrationStatus.PROVISIONAL,
        WorkspaceRegistrationStatus.ACTIVE,
        WorkspaceRegistrationStatus.SUSPENDED,
        WorkspaceRegistrationStatus.DELETED,
        None,
    ],
)
def test_non_deletion_states_reject_deletion_timestamps(
    database_session: Session,
    status: WorkspaceRegistrationStatus | None,
) -> None:
    for fields in (
        {"deletion_requested_at": NOW},
        {"deletion_scheduled_for": LATER},
        {"deletion_requested_at": NOW, "deletion_scheduled_for": LATER},
    ):
        assert_rejected(database_session, make_workspace(registration_status=status, **fields))


def test_deletion_pending_requires_both_timestamps(database_session: Session) -> None:
    pending = WorkspaceRegistrationStatus.DELETION_PENDING

    for fields in (
        {},
        {"deletion_requested_at": NOW},
        {"deletion_scheduled_for": LATER},
    ):
        assert_rejected(database_session, make_workspace(registration_status=pending, **fields))

    database_session.add(
        make_workspace(
            registration_status=pending,
            deletion_requested_at=NOW,
            deletion_scheduled_for=LATER,
        )
    )
    database_session.flush()


def test_scheduled_time_must_follow_request_time(database_session: Session) -> None:
    pending = WorkspaceRegistrationStatus.DELETION_PENDING

    for scheduled in (NOW, NOW - timedelta(seconds=1)):
        assert_rejected(
            database_session,
            make_workspace(
                registration_status=pending,
                deletion_requested_at=NOW,
                deletion_scheduled_for=scheduled,
            ),
        )


def test_auth_session_purpose_defaults_to_standard_and_is_not_null(
    database_session: Session,
) -> None:
    column = AuthSession.__table__.c.purpose
    assert column.nullable is False
    assert column.server_default is not None

    workspace = make_workspace(registration_status=WorkspaceRegistrationStatus.ACTIVE)
    database_session.add(workspace)
    database_session.flush()

    user = User(
        username=f"u-{uuid4().hex[:12]}",
        display_name="Deletion Schema",
        workspace_id=workspace.id,
        email=None,
        password_hash="$argon2id$synthetic",
        role=UserRole.SPECTATOR,
        is_active=True,
    )
    database_session.add(user)
    database_session.flush()

    def insert_session(purpose_sql: str) -> None:
        with database_session.begin_nested():
            database_session.execute(
                text(
                    "INSERT INTO auth_sessions (user_id, session_token_hash, "
                    "csrf_token_hash, expires_at"
                    + (", purpose" if purpose_sql else "")
                    + ") VALUES (:u, :t, 'c', :e"
                    + (f", {purpose_sql}" if purpose_sql else "")
                    + ")"
                ),
                {"u": user.id, "t": uuid4().hex + uuid4().hex, "e": LATER},
            )

    insert_session("")
    insert_session("'deletion_recovery'")
    with pytest.raises(IntegrityError):
        insert_session("NULL")

    purposes = database_session.execute(
        text("SELECT purpose::text FROM auth_sessions WHERE user_id = :u ORDER BY id"),
        {"u": user.id},
    ).scalars()
    assert list(purposes) == ["standard", "deletion_recovery"]

    standard = AuthSession(
        user_id=user.id,
        session_token_hash=uuid4().hex + uuid4().hex,
        csrf_token_hash="c",
        expires_at=LATER,
    )
    database_session.add(standard)
    database_session.flush()
    assert standard.purpose is AuthSessionPurpose.STANDARD


def test_workspace_deletion_requested_revocation_reason_is_valid(
    database_session: Session,
) -> None:
    workspace = make_workspace(registration_status=WorkspaceRegistrationStatus.ACTIVE)
    database_session.add(workspace)
    database_session.flush()

    user = User(
        username=f"u-{uuid4().hex[:12]}",
        display_name="Deletion Schema",
        workspace_id=workspace.id,
        email=None,
        password_hash="$argon2id$synthetic",
        role=UserRole.SPECTATOR,
        is_active=True,
    )
    database_session.add(user)
    database_session.flush()

    session_row = AuthSession(
        user_id=user.id,
        session_token_hash=uuid4().hex + uuid4().hex,
        csrf_token_hash="c",
        expires_at=LATER,
        purpose=AuthSessionPurpose.DELETION_RECOVERY,
        revoked_at=NOW,
        revocation_reason=SessionRevocationReason.WORKSPACE_DELETION_REQUESTED,
    )
    database_session.add(session_row)
    database_session.flush()
    database_session.refresh(session_row)

    assert session_row.revocation_reason is SessionRevocationReason.WORKSPACE_DELETION_REQUESTED
    assert session_row.purpose is AuthSessionPurpose.DELETION_RECOVERY


def test_deletion_record_has_no_installation_foreign_key() -> None:
    table = WorkspaceDeletionRecord.__table__
    assert not any(isinstance(c, ForeignKey) for column in table.c for c in column.foreign_keys)
    assert table.foreign_keys == set()
    assert inspect(engine).get_foreign_keys("workspace_deletion_records") == []


def test_deletion_record_columns_and_indexes() -> None:
    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("workspace_deletion_records")
    }

    required = {
        "id",
        "former_workspace_id",
        "provider",
        "normalized_provider_league_id",
        "requested_at",
        "scheduled_for",
        "completed_at",
        "outcome",
        "retention_expires_at",
    }
    assert required <= set(columns)
    for name in required:
        assert columns[name]["nullable"] is False
    for name in ("requested_at", "scheduled_for", "completed_at", "retention_expires_at"):
        assert columns[name]["type"].timezone is True
    assert columns["normalized_provider_league_id"]["type"].length == 255

    assert not FORBIDDEN_COLUMNS & set(columns)

    indexes = {
        index["name"]: index for index in inspect(engine).get_indexes("workspace_deletion_records")
    }
    assert indexes["ix_workspace_deletion_records_former_workspace_id"]["column_names"] == [
        "former_workspace_id"
    ]
    assert indexes["ix_workspace_deletion_records_retention_expires_at"]["column_names"] == [
        "retention_expires_at"
    ]
    assert indexes["ix_workspace_deletion_records_provider_identity"]["column_names"] == [
        "provider",
        "normalized_provider_league_id",
    ]
    assert not any(index["unique"] for index in indexes.values())
    assert not inspect(engine).get_unique_constraints("workspace_deletion_records")


def test_deletion_record_timestamp_ordering_is_enforced(database_session: Session) -> None:
    assert_rejected(database_session, make_record(scheduled_for=NOW))
    assert_rejected(database_session, make_record(scheduled_for=NOW - timedelta(days=1)))
    assert_rejected(database_session, make_record(completed_at=LATER - timedelta(seconds=1)))
    completed = LATER + timedelta(minutes=5)
    assert_rejected(database_session, make_record(retention_expires_at=completed))
    assert_rejected(
        database_session, make_record(retention_expires_at=completed - timedelta(days=1))
    )

    # completed_at == scheduled_for is allowed.
    database_session.add(make_record(completed_at=LATER, retention_expires_at=LATER + timedelta(1)))
    database_session.flush()


@pytest.mark.parametrize("league_id", ["", "   "])
def test_deletion_record_rejects_blank_league_id(
    database_session: Session,
    league_id: str,
) -> None:
    assert_rejected(database_session, make_record(normalized_provider_league_id=league_id))


def test_deletion_record_outcome_only_supports_completed(database_session: Session) -> None:
    with pytest.raises(IntegrityError), database_session.begin_nested():
        database_session.execute(
            text(
                "INSERT INTO workspace_deletion_records (former_workspace_id, provider, "
                "normalized_provider_league_id, requested_at, scheduled_for, "
                "completed_at, outcome, retention_expires_at) VALUES "
                "(1, 'espn', '1', :r, :s, :c, 'failed', :x)"
            ),
            {
                "r": NOW,
                "s": LATER,
                "c": LATER,
                "x": LATER + timedelta(days=1),
            },
        )

    database_session.add(make_record())
    database_session.flush()


def test_multiple_historical_records_are_allowed(database_session: Session) -> None:
    database_session.add_all([make_record(), make_record(), make_record(former_workspace_id=124)])
    database_session.flush()

    count = database_session.execute(
        text(
            "SELECT count(*) FROM workspace_deletion_records "
            "WHERE provider = 'espn' AND normalized_provider_league_id = '424242'"
        )
    ).scalar_one()
    assert count >= 3
