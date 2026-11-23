"""Database constraints for validated provider targets (revision b7d41e9c2a10)."""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    ProviderAccessMode,
)
from app.db.platform_models import Installation, ProviderConnection
from app.db.session import SessionFactory, engine

CIPHERTEXT = "synthetic-ciphertext-not-real"


@pytest.fixture
def workspace_id() -> Iterator[int]:
    slug = f"target-schema-{uuid4().hex[:10]}"

    with SessionFactory() as session:
        workspace = Installation(
            application_name="Target Schema",
            slug=slug,
            normalized_slug=slug,
            timezone="America/Chicago",
            status=InstallationStatus.SETUP_IN_PROGRESS,
        )
        session.add(workspace)
        session.commit()
        created_id = workspace.id

    yield created_id

    with SessionFactory() as session:
        workspace = session.get(Installation, created_id)

        if workspace is not None:
            session.delete(workspace)
            session.commit()


def insert(workspace_id: int, **fields: Any) -> None:
    values: dict[str, Any] = {
        "installation_id": workspace_id,
        "provider": FantasyProvider.ESPN,
        "display_name": "ESPN",
        "is_active": True,
    }
    values.update(fields)

    with SessionFactory() as session:
        session.add(ProviderConnection(**values))
        session.commit()


PUBLIC = {
    "access_mode": ProviderAccessMode.PUBLIC,
    "provider_league_id": "424242",
    "provider_season_year": 2024,
}
PRIVATE = {
    "access_mode": ProviderAccessMode.PRIVATE,
    "provider_league_id": "424242",
    "provider_season_year": 2024,
    "encrypted_credentials": CIPHERTEXT,
    "credential_version": 1,
}


def test_schema_introspection() -> None:
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("provider_connections")}

    for name in ("provider_league_id", "provider_season_year", "access_mode", "credential_version"):
        assert columns[name]["nullable"] is True

    assert columns["credential_version"]["default"] is None

    constraint_names = {
        constraint["name"] for constraint in inspector.get_check_constraints("provider_connections")
    }
    assert {
        "ck_provider_connections_credential_pair",
        "ck_provider_connections_access_mode_state",
        "ck_provider_connections_season_year_range",
    } <= constraint_names

    indexes = {index["name"]: index for index in inspector.get_indexes("provider_connections")}
    active_index = indexes["uq_provider_connections_active_installation"]
    assert active_index["unique"] is True
    assert active_index["column_names"] == ["installation_id"]

    with engine.connect() as connection:
        labels = connection.scalars(
            text(
                "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'provider_access_mode' ORDER BY e.enumsortorder"
            )
        ).all()
        predicate = connection.scalar(
            text(
                "SELECT pg_get_expr(i.indpred, i.indrelid) FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'uq_provider_connections_active_installation'"
            )
        )

    assert labels == ["public", "private"]
    assert "is_active IS TRUE" in predicate


def test_public_row_without_credentials_is_valid(workspace_id: int) -> None:
    insert(workspace_id, **PUBLIC)


def test_private_row_with_credentials_is_valid(workspace_id: int) -> None:
    insert(workspace_id, **PRIVATE)


def test_legacy_rows_with_and_without_credentials_are_valid(workspace_id: int) -> None:
    insert(workspace_id, is_active=False)
    insert(workspace_id, encrypted_credentials=CIPHERTEXT, credential_version=1)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {**PUBLIC, "encrypted_credentials": CIPHERTEXT, "credential_version": 1},
            id="public-with-credentials",
        ),
        pytest.param(
            {**PRIVATE, "encrypted_credentials": None, "credential_version": None},
            id="private-without-credentials",
        ),
        pytest.param(
            {"provider_league_id": "1", "provider_season_year": 2024},
            id="target-without-access-mode",
        ),
        pytest.param(
            {"access_mode": ProviderAccessMode.PUBLIC, "provider_season_year": 2024},
            id="access-mode-without-league",
        ),
        pytest.param(
            {"access_mode": ProviderAccessMode.PUBLIC, "provider_league_id": "1"},
            id="access-mode-without-season",
        ),
        pytest.param({"encrypted_credentials": CIPHERTEXT}, id="ciphertext-without-version"),
        pytest.param({"credential_version": 1}, id="version-without-ciphertext"),
        pytest.param({**PUBLIC, "provider_season_year": 1999}, id="season-below-range"),
        pytest.param({**PUBLIC, "provider_season_year": 2101}, id="season-above-range"),
    ],
)
def test_invalid_states_raise_integrity_error(workspace_id: int, fields: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        insert(workspace_id, **fields)


def test_second_active_connection_is_rejected_for_any_provider(workspace_id: int) -> None:
    insert(workspace_id, **PUBLIC)

    with pytest.raises(IntegrityError):
        insert(workspace_id, **{**PUBLIC, "provider": FantasyProvider.SLEEPER})


def test_inactive_historical_connections_remain_allowed(workspace_id: int) -> None:
    insert(workspace_id, **PUBLIC)
    insert(workspace_id, **{**PUBLIC, "is_active": False})
    insert(workspace_id, **{**PRIVATE, "is_active": False})
