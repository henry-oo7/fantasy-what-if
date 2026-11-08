from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import configure_mappers

from app.db import model_registry  # noqa: F401
from app.db.base import Base
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)


def test_canonical_enum_values() -> None:
    assert [member.value for member in FantasyProvider] == [
        "espn",
        "sleeper",
        "yahoo",
    ]

    assert [member.value for member in InstallationStatus] == [
        "setup_required",
        "setup_in_progress",
        "active",
    ]

    assert [member.value for member in SeasonStatus] == [
        "draft",
        "active",
        "completed",
        "archived",
    ]

    assert [member.value for member in FantasySport] == [
        "football",
    ]


def test_platform_tables_are_registered() -> None:
    assert "installations" in Base.metadata.tables
    assert "provider_connections" in Base.metadata.tables


def test_installation_defaults() -> None:
    table = Installation.__table__

    assert table.c.timezone.server_default.arg == "America/Chicago"
    assert table.c.status.server_default.arg == "setup_required"


def test_provider_connection_defaults() -> None:
    table = ProviderConnection.__table__

    assert table.c.credential_version.server_default.arg == "1"
    assert table.c.is_active.server_default.arg == "true"


def test_platform_enum_database_names() -> None:
    installation_status = Installation.__table__.c.status.type
    provider = ProviderConnection.__table__.c.provider.type
    league_provider = League.__table__.c.provider.type
    league_sport = League.__table__.c.sport.type
    season_status = Season.__table__.c.status.type

    assert isinstance(installation_status, Enum)
    assert isinstance(provider, Enum)
    assert isinstance(league_provider, Enum)
    assert isinstance(league_sport, Enum)
    assert isinstance(season_status, Enum)

    assert installation_status.name == "installation_status"
    assert provider.name == "fantasy_provider"
    assert league_provider.name == "fantasy_provider"
    assert league_sport.name == "fantasy_sport"
    assert season_status.name == "season_status"


def test_transition_fields_remain_nullable() -> None:
    league = League.__table__
    season = Season.__table__

    assert league.c.espn_league_id.nullable
    assert league.c.installation_id.nullable
    assert league.c.provider.nullable
    assert league.c.provider_league_id.nullable
    assert league.c.sport.nullable

    assert season.c.provider_connection_id.nullable
    assert season.c.provider_season_id.nullable
    assert season.c.status.nullable
    assert season.c.team_count.nullable


def test_canonical_foreign_key_delete_rules() -> None:
    installation_foreign_key = next(iter(League.__table__.c.installation_id.foreign_keys))
    provider_foreign_key = next(iter(Season.__table__.c.provider_connection_id.foreign_keys))

    assert isinstance(
        installation_foreign_key,
        ForeignKey,
    )
    assert isinstance(
        provider_foreign_key,
        ForeignKey,
    )

    assert installation_foreign_key.target_fullname == "installations.id"
    assert installation_foreign_key.ondelete == "CASCADE"

    assert provider_foreign_key.target_fullname == "provider_connections.id"
    assert provider_foreign_key.ondelete == "RESTRICT"


def test_canonical_relationships_configure() -> None:
    configure_mappers()

    assert Installation.leagues.property.back_populates == "installation"
    assert League.installation.property.back_populates == "leagues"
    assert ProviderConnection.seasons.property.back_populates == "provider_connection"
    assert Season.provider_connection.property.back_populates == "seasons"


def test_season_capability_defaults_are_false() -> None:
    season = Season.__table__

    capability_columns = (
        "supports_head_to_head",
        "supports_weekly_matchups",
        "supports_lineup_history",
        "supports_optimal_lineups",
        "supports_schedule_swap",
        "supports_all_play",
        "supports_league_average",
        "supports_team_logos",
    )

    for column_name in capability_columns:
        column = season.c[column_name]

        assert column.nullable is False
        assert column.server_default is not None
        assert column.server_default.arg == "false"
