from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import configure_mappers

from app.db import model_registry  # noqa: F401
from app.db.models import (
    League,
    Season,
    SyncRun,
)
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)


def test_installation_active_references() -> None:
    table = Installation.__table__

    assert table.c.active_league_id.nullable is True
    assert table.c.active_season_id.nullable is True

    league_foreign_key = next(iter(table.c.active_league_id.foreign_keys))
    season_foreign_key = next(iter(table.c.active_season_id.foreign_keys))

    assert league_foreign_key.target_fullname == "leagues.id"
    assert league_foreign_key.ondelete == "SET NULL"

    assert season_foreign_key.target_fullname == "seasons.id"
    assert season_foreign_key.ondelete == "SET NULL"


def test_installation_active_relationships() -> None:
    configure_mappers()

    assert Installation.active_league.property.mapper.class_ is League
    assert Installation.active_season.property.mapper.class_ is Season

    league_foreign_keys = {
        column.name for column in (Installation.active_league.property.local_columns)
    }
    season_foreign_keys = {
        column.name for column in (Installation.active_season.property.local_columns)
    }

    assert league_foreign_keys == {
        "active_league_id",
    }
    assert season_foreign_keys == {
        "active_season_id",
    }


def test_installation_league_collection_is_disambiguated() -> None:
    configure_mappers()

    assert Installation.leagues.property.back_populates == "installation"
    assert League.installation.property.back_populates == "leagues"

    installation_foreign_keys = {
        column.name for column in (League.installation.property.local_columns)
    }

    assert installation_foreign_keys == {
        "installation_id",
    }


def test_sync_run_provider_fields() -> None:
    table = SyncRun.__table__

    assert table.c.provider_connection_id.nullable is True
    assert table.c.provider.nullable is True
    assert table.c.provider_request_metadata.nullable is True

    assert isinstance(
        table.c.provider_request_metadata.type,
        JSONB,
    )

    assert table.c.analytics_rebuilt.nullable is False
    assert table.c.analytics_rebuilt.server_default.arg == "false"


def test_sync_run_provider_foreign_key() -> None:
    table = SyncRun.__table__

    foreign_key = next(iter(table.c.provider_connection_id.foreign_keys))

    assert foreign_key.target_fullname == "provider_connections.id"
    assert foreign_key.ondelete == "SET NULL"


def test_sync_run_provider_relationships() -> None:
    configure_mappers()

    assert ProviderConnection.sync_runs.property.back_populates == "provider_connection"
    assert SyncRun.provider_connection.property.back_populates == "sync_runs"


def test_one_active_season_partial_index() -> None:
    table = Season.__table__

    active_index = next(
        (index for index in table.indexes if index.name == "uq_seasons_one_active_per_league"),
        None,
    )

    assert isinstance(active_index, Index)
    assert active_index.unique is True

    indexed_columns = [column.name for column in active_index.columns]

    assert indexed_columns == ["league_id"]

    where_clause = str(active_index.dialect_options["postgresql"]["where"])

    assert where_clause == "status = 'active'::season_status"


def test_existing_season_uniqueness_remains() -> None:
    constraint_names = {constraint.name for constraint in Season.__table__.constraints}

    assert "uq_seasons_league_year" in constraint_names
