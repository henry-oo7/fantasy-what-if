"""Rotate stored provider credential envelopes to the current key."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import model_registry  # noqa: F401
from app.db.platform_models import ProviderConnection
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
from app.providers.credential_vault import (
    ProviderCredentialVault,
)

DEFAULT_ROTATION_BATCH_SIZE = 100


class ProviderCredentialKeyRotationError(RuntimeError):
    """A provider credential envelope could not be rotated safely."""


@dataclass(frozen=True, slots=True)
class ProviderCredentialKeyRotationResult:
    """Safe summary of one bounded credential-rotation batch."""

    selected_count: int
    rotated_count: int
    current_version: int
    rotated_connection_ids: tuple[int, ...]
    remaining_count: int

    @property
    def complete(self) -> bool:
        """Return whether no outdated envelopes remain."""

        return self.remaining_count == 0

    @property
    def blocked(self) -> bool:
        """Return whether remaining rows were locked elsewhere."""

        return self.selected_count == 0 and self.remaining_count > 0


def rotate_provider_credential_key_batch(
    session: Session,
    *,
    credential_keyring: ProviderCredentialKeyring,
    batch_size: int = DEFAULT_ROTATION_BATCH_SIZE,
) -> ProviderCredentialKeyRotationResult:
    """Rotate one locked batch to the configured current key."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive.")

    if not credential_keyring.configured:
        raise ProviderCredentialKeyRotationError(
            "Provider credential encryption is not configured."
        )

    current_version = credential_keyring.current_version
    credential_keyring.key_for_version(current_version)

    connections = session.scalars(
        select(ProviderConnection)
        .where(
            ProviderConnection.encrypted_credentials.is_not(None),
            ProviderConnection.credential_version != current_version,
        )
        .order_by(ProviderConnection.id)
        .with_for_update(skip_locked=True)
        .limit(batch_size)
    ).all()

    if not connections:
        remaining_count = int(
            session.scalar(
                select(func.count(ProviderConnection.id)).where(
                    ProviderConnection.encrypted_credentials.is_not(None),
                    ProviderConnection.credential_version != current_version,
                )
            )
            or 0
        )

        return ProviderCredentialKeyRotationResult(
            selected_count=0,
            rotated_count=0,
            current_version=current_version,
            rotated_connection_ids=(),
            remaining_count=remaining_count,
        )

    vault = ProviderCredentialVault(
        keys=credential_keyring.keys,
    )
    prepared_rotations: list[tuple[ProviderConnection, str]] = []

    for connection in connections:
        encrypted_credentials = connection.encrypted_credentials

        if encrypted_credentials is None:
            raise ProviderCredentialKeyRotationError(
                "A selected provider connection has no credential envelope."
            )

        try:
            credential_keyring.key_for_version(connection.credential_version)

            rotated_envelope = vault.rotate(
                encrypted_credentials=encrypted_credentials,
                current_key_version=(connection.credential_version),
                new_key_version=current_version,
                expected_provider=connection.provider,
            )

            vault.decrypt(
                encrypted_credentials=rotated_envelope,
                key_version=current_version,
                expected_provider=connection.provider,
                expected_schema_version=(connection.credential_schema_version),
            )
        except Exception as exc:
            raise ProviderCredentialKeyRotationError(
                "Provider credential rotation failed for "
                f"connection {connection.id}. The existing "
                "envelope was preserved."
            ) from exc

        prepared_rotations.append(
            (
                connection,
                rotated_envelope,
            )
        )

    for connection, rotated_envelope in prepared_rotations:
        connection.encrypted_credentials = rotated_envelope
        connection.credential_version = current_version

    session.flush()

    rotated_ids = tuple(connection.id for connection, _ in prepared_rotations)

    remaining_count = int(
        session.scalar(
            select(func.count(ProviderConnection.id)).where(
                ProviderConnection.encrypted_credentials.is_not(None),
                ProviderConnection.credential_version != current_version,
            )
        )
        or 0
    )

    return ProviderCredentialKeyRotationResult(
        selected_count=len(connections),
        rotated_count=len(prepared_rotations),
        current_version=current_version,
        rotated_connection_ids=rotated_ids,
        remaining_count=remaining_count,
    )


def rotate_provider_credential_key_batch_committed(
    *,
    session_factory: sessionmaker[Session],
    credential_keyring: ProviderCredentialKeyring,
    batch_size: int = DEFAULT_ROTATION_BATCH_SIZE,
) -> ProviderCredentialKeyRotationResult:
    """Rotate and commit one bounded credential batch."""

    with session_factory() as session:
        try:
            result = rotate_provider_credential_key_batch(
                session,
                credential_keyring=credential_keyring,
                batch_size=batch_size,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

    return result
