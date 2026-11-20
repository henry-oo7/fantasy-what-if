from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql

from app.db.platform_enums import FantasyProvider
from app.db.platform_models import ProviderConnection
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.services.provider_credential_key_rotation import (
    ProviderCredentialKeyRotationError,
    rotate_provider_credential_key_batch,
    rotate_provider_credential_key_batch_committed,
)


def make_key() -> str:
    return Fernet.generate_key().decode("ascii")


def make_keyring(
    *,
    first_key: str,
    second_key: str,
) -> ProviderCredentialKeyring:
    return ProviderCredentialKeyring(
        keys={
            1: first_key,
            2: second_key,
        },
        current_version=2,
    )


def make_connection(
    *,
    connection_id: int,
    encrypted_credentials: str,
    credential_version: int = 1,
) -> ProviderConnection:
    return ProviderConnection(
        id=connection_id,
        installation_id=connection_id,
        provider=FantasyProvider.ESPN,
        display_name=f"ESPN {connection_id}",
        encrypted_credentials=encrypted_credentials,
        credential_version=credential_version,
        credential_schema_version=1,
        is_active=True,
    )


def encrypt_version_one(
    *,
    key: str,
    marker: str,
) -> str:
    return ProviderCredentialVault(
        keys={
            1: key,
        }
    ).encrypt(
        provider=FantasyProvider.ESPN,
        credentials=ProviderCredentials(
            values={
                "league_id": marker,
                "season": "2026",
                "espn_s2": f"{marker}-cookie",
                "swid": f"{{{marker}-swid}}",
            }
        ),
        key_version=1,
    )


def test_rotation_batch_reencrypts_and_verifies_rows() -> None:
    first_key = make_key()
    second_key = make_key()
    keyring = make_keyring(
        first_key=first_key,
        second_key=second_key,
    )
    first = make_connection(
        connection_id=10,
        encrypted_credentials=encrypt_version_one(
            key=first_key,
            marker="first",
        ),
    )
    second = make_connection(
        connection_id=20,
        encrypted_credentials=encrypt_version_one(
            key=first_key,
            marker="second",
        ),
    )
    session = MagicMock()
    session.scalars.return_value.all.return_value = [
        first,
        second,
    ]
    session.scalar.return_value = 0

    result = rotate_provider_credential_key_batch(
        session,
        credential_keyring=keyring,
        batch_size=2,
    )

    assert result.selected_count == 2
    assert result.rotated_count == 2
    assert result.current_version == 2
    assert result.rotated_connection_ids == (10, 20)
    assert result.remaining_count == 0
    assert result.complete is True
    assert result.blocked is False
    assert first.credential_version == 2
    assert second.credential_version == 2

    vault = ProviderCredentialVault(
        keys=keyring.keys,
    )
    first_envelope = vault.decrypt(
        encrypted_credentials=first.encrypted_credentials,
        key_version=2,
        expected_provider=FantasyProvider.ESPN,
    )
    second_envelope = vault.decrypt(
        encrypted_credentials=second.encrypted_credentials,
        key_version=2,
        expected_provider=FantasyProvider.ESPN,
    )

    assert first_envelope.credentials.values["league_id"] == "first"
    assert second_envelope.credentials.values["league_id"] == "second"
    session.flush.assert_called_once_with()


def test_rotation_query_is_bounded_and_skip_locked() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    session.scalar.return_value = 0
    keyring = ProviderCredentialKeyring(
        keys={
            2: make_key(),
        },
        current_version=2,
    )

    result = rotate_provider_credential_key_batch(
        session,
        credential_keyring=keyring,
        batch_size=17,
    )

    statement = session.scalars.call_args.args[0]
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "FOR UPDATE SKIP LOCKED" in compiled
    assert "LIMIT 17" in compiled
    assert "credential_version != 2" in compiled
    assert "encrypted_credentials IS NOT NULL" in compiled
    assert result.complete is True
    session.flush.assert_not_called()


def test_locked_outdated_rows_are_not_reported_complete() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    session.scalar.return_value = 3
    keyring = ProviderCredentialKeyring(
        keys={
            2: make_key(),
        },
        current_version=2,
    )

    result = rotate_provider_credential_key_batch(
        session,
        credential_keyring=keyring,
    )

    assert result.selected_count == 0
    assert result.rotated_count == 0
    assert result.remaining_count == 3
    assert result.complete is False
    assert result.blocked is True
    session.flush.assert_not_called()


def test_current_rows_are_idempotently_excluded() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    session.scalar.return_value = 0
    keyring = ProviderCredentialKeyring(
        keys={
            2: make_key(),
        },
        current_version=2,
    )

    first = rotate_provider_credential_key_batch(
        session,
        credential_keyring=keyring,
    )
    second = rotate_provider_credential_key_batch(
        session,
        credential_keyring=keyring,
    )

    assert first.complete is True
    assert second.complete is True
    assert first.rotated_count == 0
    assert second.rotated_count == 0


def test_failed_batch_preserves_all_old_envelopes() -> None:
    first_key = make_key()
    second_key = make_key()
    keyring = make_keyring(
        first_key=first_key,
        second_key=second_key,
    )
    valid_envelope = encrypt_version_one(
        key=first_key,
        marker="valid",
    )
    first = make_connection(
        connection_id=10,
        encrypted_credentials=valid_envelope,
    )
    second = make_connection(
        connection_id=20,
        encrypted_credentials="corrupted-envelope",
    )
    session = MagicMock()
    session.scalars.return_value.all.return_value = [
        first,
        second,
    ]

    with pytest.raises(
        ProviderCredentialKeyRotationError,
        match="connection 20",
    ):
        rotate_provider_credential_key_batch(
            session,
            credential_keyring=keyring,
        )

    assert first.encrypted_credentials == valid_envelope
    assert first.credential_version == 1
    assert second.encrypted_credentials == ("corrupted-envelope")
    assert second.credential_version == 1
    session.flush.assert_not_called()


def test_unconfigured_keyring_is_rejected() -> None:
    session = MagicMock()
    keyring = ProviderCredentialKeyring(
        keys={},
        current_version=1,
    )

    with pytest.raises(
        ProviderCredentialKeyRotationError,
        match="not configured",
    ):
        rotate_provider_credential_key_batch(
            session,
            credential_keyring=keyring,
        )

    session.scalars.assert_not_called()


def test_batch_size_must_be_positive() -> None:
    session = MagicMock()
    keyring = ProviderCredentialKeyring(
        keys={
            1: make_key(),
        },
        current_version=1,
    )

    with pytest.raises(
        ValueError,
        match="batch_size",
    ):
        rotate_provider_credential_key_batch(
            session,
            credential_keyring=keyring,
            batch_size=0,
        )


def test_committed_wrapper_commits_success() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    session.scalar.return_value = 0
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session
    keyring = ProviderCredentialKeyring(
        keys={
            1: make_key(),
        },
        current_version=1,
    )

    result = rotate_provider_credential_key_batch_committed(
        session_factory=session_factory,
        credential_keyring=keyring,
    )

    assert result.complete is True
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


def test_committed_wrapper_rolls_back_failure() -> None:
    first_key = make_key()
    second_key = make_key()
    connection = make_connection(
        connection_id=10,
        encrypted_credentials="corrupted-envelope",
    )
    session = MagicMock()
    session.scalars.return_value.all.return_value = [
        connection,
    ]
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    with pytest.raises(
        ProviderCredentialKeyRotationError,
    ):
        rotate_provider_credential_key_batch_committed(
            session_factory=session_factory,
            credential_keyring=make_keyring(
                first_key=first_key,
                second_key=second_key,
            ),
        )

    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()
    assert connection.encrypted_credentials == ("corrupted-envelope")
    assert connection.credential_version == 1
