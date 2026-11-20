from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.models import League
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import (
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.db.session import engine
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
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
) -> tuple[Session, ProviderCredentialKeyring]:
    credential_key = Fernet.generate_key().decode("ascii")
    keyring = ProviderCredentialKeyring(
        keys={
            1: credential_key,
        },
        current_version=1,
    )
    vault = ProviderCredentialVault(
        keys=keyring.keys,
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

    league = database_session.scalar(
        select(League).where(
            League.installation_id == 1,
        )
    )

    assert league is not None
    assert league.provider is not None
    assert league.provider_league_id is not None

    claim = database_session.scalar(
        select(ProviderLeagueClaim).where(
            ProviderLeagueClaim.league_id == league.id,
        )
    )

    if claim is None:
        provider_league_id = league.provider_league_id.strip()

        claim = ProviderLeagueClaim(
            workspace_id=league.installation_id,
            league_id=league.id,
            provider=league.provider,
            provider_league_id=provider_league_id,
            normalized_provider_league_id=(provider_league_id),
        )
        database_session.add(claim)

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

    return database_session, keyring


def check_by_name(
    audit: SaasMigrationAudit,
    name: str,
):
    return next(check for check in audit.checks if check.name == name)


def test_valid_v1_database_passes_migration_audit(
    migration_ready_database: tuple[Session, ProviderCredentialKeyring],
) -> None:
    session, keyring = migration_ready_database

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_keyring=keyring,
    )

    assert audit.passed is True
    assert len(audit.checks) == 26
    assert all(check.passed for check in audit.checks)


def test_missing_administrator_email_blocks_migration(
    migration_ready_database: tuple[Session, ProviderCredentialKeyring],
) -> None:
    session, keyring = migration_ready_database

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
        provider_credential_keyring=keyring,
    )

    check = check_by_name(
        audit,
        "administrator_email",
    )

    assert audit.passed is False
    assert check.passed is False
    assert check.message == ("Administrator email is missing or invalid.")


def test_missing_credential_key_blocks_migration(
    migration_ready_database: tuple[Session, ProviderCredentialKeyring],
) -> None:
    session, _keyring = migration_ready_database

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_keyring=ProviderCredentialKeyring(
            keys={},
            current_version=1,
        ),
    )

    check = check_by_name(
        audit,
        "credentials_decryptable",
    )

    assert audit.passed is False
    assert check.passed is False
    assert check.message == ("Provider credentials could not be decrypted with the configured key.")


def test_invalid_espn_league_identity_blocks_migration(
    migration_ready_database: tuple[Session, ProviderCredentialKeyring],
) -> None:
    session, keyring = migration_ready_database

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
        provider_credential_keyring=keyring,
    )

    check = check_by_name(
        audit,
        "espn_league_identity",
    )

    assert audit.passed is False
    assert check.passed is False
    assert check.message == ("The active league does not have a valid decimal ESPN league ID.")


def test_mismatched_provider_claim_identity_blocks_migration(
    migration_ready_database: tuple[Session, ProviderCredentialKeyring],
) -> None:
    session, keyring = migration_ready_database

    claim = session.scalar(select(ProviderLeagueClaim))

    assert claim is not None

    claim.normalized_provider_league_id = "different-league-id"

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_keyring=keyring,
    )

    check = check_by_name(
        audit,
        "provider_league_claim_identity_match",
    )

    assert audit.passed is False
    assert check.passed is False
    assert check.message == ("A provider identity claim does not match its league identifier.")


def test_unattached_provider_claim_blocks_migration(
    migration_ready_database: tuple[Session, ProviderCredentialKeyring],
) -> None:
    session, keyring = migration_ready_database

    claim = session.scalar(select(ProviderLeagueClaim))

    assert claim is not None

    claim.league_id = None

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_keyring=keyring,
    )

    check = check_by_name(
        audit,
        "provider_league_claim_attachment",
    )

    assert audit.passed is False
    assert check.passed is False
    assert check.message == ("One or more provider identity claims are not attached to a league.")


def test_historical_key_version_passes_migration_audit(
    migration_ready_database: tuple[
        Session,
        ProviderCredentialKeyring,
    ],
) -> None:
    session, first_keyring = migration_ready_database
    second_key = Fernet.generate_key().decode("ascii")

    keyring = ProviderCredentialKeyring(
        keys={
            1: first_keyring.key_for_version(1),
            2: second_key,
        },
        current_version=2,
    )

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_keyring=keyring,
    )

    check = check_by_name(
        audit,
        "credentials_decryptable",
    )

    assert check.passed is True


def test_missing_historical_key_version_blocks_migration(
    migration_ready_database: tuple[
        Session,
        ProviderCredentialKeyring,
    ],
) -> None:
    session, _first_keyring = migration_ready_database

    keyring = ProviderCredentialKeyring(
        keys={
            2: Fernet.generate_key().decode("ascii"),
        },
        current_version=2,
    )

    audit = audit_saas_migration_readiness(
        session,
        provider_credential_keyring=keyring,
    )

    check = check_by_name(
        audit,
        "credentials_decryptable",
    )

    assert check.passed is False
    assert check.message == ("Provider credentials could not be decrypted with the configured key.")
