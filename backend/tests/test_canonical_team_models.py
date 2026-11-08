from sqlalchemy.orm import configure_mappers

from app.db import model_registry  # noqa: F401
from app.db.base import Base
from app.db.models import (
    Franchise,
    League,
    PlayerWeekScore,
    Team,
)


def test_franchise_table_is_registered() -> None:
    assert "franchises" in Base.metadata.tables


def test_franchise_fields() -> None:
    table = Franchise.__table__

    assert table.c.league_id.nullable is False
    assert table.c.display_name.nullable is False
    assert table.c.display_name.type.length == 255
    assert table.c.logo_url.nullable is True
    assert table.c.logo_url.type.length == 1000
    assert table.c.is_active.nullable is False
    assert table.c.is_active.server_default is not None
    assert table.c.is_active.server_default.arg == "true"


def test_franchise_league_foreign_key() -> None:
    foreign_key = next(iter(Franchise.__table__.c.league_id.foreign_keys))

    assert foreign_key.target_fullname == "leagues.id"
    assert foreign_key.ondelete == "CASCADE"


def test_franchise_relationships_configure() -> None:
    configure_mappers()

    assert League.franchises.property.back_populates == "league"
    assert Franchise.league.property.back_populates == "franchises"
    assert Franchise.season_teams.property.back_populates == "franchise"
    assert Team.franchise.property.back_populates == "season_teams"


def test_canonical_team_fields() -> None:
    table = Team.__table__

    assert table.c.provider_team_id.nullable is True
    assert table.c.provider_team_id.type.length == 255

    assert table.c.franchise_id.nullable is True

    assert table.c.abbreviation.nullable is True
    assert table.c.abbreviation.type.length == 50

    assert table.c.owner_display_name.nullable is True
    assert table.c.owner_display_name.type.length == 255

    assert table.c.logo_url.nullable is True
    assert table.c.logo_url.type.length == 1000

    assert table.c.is_active.nullable is False
    assert table.c.is_active.server_default is not None
    assert table.c.is_active.server_default.arg == "true"


def test_team_franchise_delete_rule() -> None:
    foreign_key = next(iter(Team.__table__.c.franchise_id.foreign_keys))

    assert foreign_key.target_fullname == "franchises.id"
    assert foreign_key.ondelete == "SET NULL"


def test_canonical_player_week_fields() -> None:
    table = PlayerWeekScore.__table__

    assert table.c.provider_player_id.nullable is True
    assert table.c.provider_player_id.type.length == 255

    assert table.c.provider_position.nullable is True
    assert table.c.provider_position.type.length == 100


def test_slice_a2_metadata_inventory() -> None:

    required_tables = {
        "franchises",
        "teams",
        "player_week_scores",
    }

    assert required_tables.issubset(Base.metadata.tables)
