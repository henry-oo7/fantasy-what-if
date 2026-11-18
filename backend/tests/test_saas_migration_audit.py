from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.models import League
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import ProviderConnection
from app.db.session import engine
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.services.saas_migration_audit import (
    SaasMigrationAudit,
    audit_saas_migration_readiness,
)


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


@pytest.fixture
def migration_ready_database(
    database_session: Session,
) -> tuple[Session, str]:
    credential_key = Fernet.generate_key().decode("ascii")
    vault = ProviderCredentialVault(
        keys={
            1: credential_key,
        }
    )

    administrator = database_session.scalar(
        select(User).where(
            User.role == UserRole.ADMIN,
        )
    )
    connection = database_session.scalar(
        select(ProviderConnection).where(
            ProviderConnection.is_active.is_(True),
        )
    )

    assert administrator is not None
    assert connection is not None
    assert connection.provider is FantasyProvider.ESPN

    administrator.email = "administrator@example.test"
    connection.credential_version = 1
    connection.encrypted_credentials = vault.encrypt(
        provider=FantasyProvider.ESPN,
        credentials=ProviderCredentials(
            values={
                "espn_s2": "synthetic-espn-s2",
                "swid": "{SYNTHETIC-SWID}",
            }
        ),
        key_version=1,
    )

    database_session.flush()

    return database_session, credential_key


def check_by_name(
    audit: SaasMigrationAudit,
    name: str,
):
    return next(check for check in audit.checks if check.name == name)


def test_valid_v1_database_passes_migration_audit(
    migration_ready_database: tuple[Session, str],
) -> None:
    session, credential_key = migration_ready_database

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_key=credential_key,
    )

    assert audit.passed is True
    assert len(audit.checks) == 19
    assert all(check.passed for check in audit.checks)


def test_missing_administrator_email_blocks_migration(
    migration_ready_database: tuple[Session, str],
) -> None:
    session, credential_key = migration_ready_database

    administrator = session.scalar(
        select(User).where(
            User.role == UserRole.ADMIN,
        )
    )

    assert administrator is not None

    administrator.email = None
    session.flush()

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_key=credential_key,
    )

    check = check_by_name(
        audit,
        "administrator_email",
    )

    assert audit.passed is False
    assert check.passed is False
    assert check.message == ("Administrator email is missing or invalid.")


def test_missing_credential_key_blocks_migration(
    migration_ready_database: tuple[Session, str],
) -> None:
    session, _ = migration_ready_database

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_key=None,
    )

    check = check_by_name(
        audit,
        "credentials_decryptable",
    )

    assert audit.passed is False
    assert check.passed is False
    assert check.message == ("Provider credentials could not be decrypted with the configured key.")


def test_invalid_espn_league_identity_blocks_migration(
    migration_ready_database: tuple[Session, str],
) -> None:
    session, credential_key = migration_ready_database

    league = session.scalar(
        select(League).where(
            League.installation_id == 1,
        )
    )

    assert league is not None

    league.provider_league_id = "invalid-league-id"
    session.flush()

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_key=credential_key,
    )

    check = check_by_name(
        audit,
        "espn_league_identity",
    )

    assert audit.passed is False
    assert check.passed is False
    assert check.message == ("The active league does not have a valid decimal ESPN league ID.")
