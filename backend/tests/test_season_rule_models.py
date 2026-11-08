from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import configure_mappers

from app.db import model_registry  # noqa: F401
from app.db.base import Base
from app.db.models import (
    LineupSlotDefinition,
    LineupSlotEligibility,
    ScheduleRule,
    ScoringRule,
    Season,
)


def constraint_names(
    table_name: str,
    constraint_type: type[CheckConstraint | UniqueConstraint],
) -> set[str | None]:
    table = Base.metadata.tables[table_name]

    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def test_dynamic_rule_tables_are_registered() -> None:
    required_tables = {
        "lineup_slot_definitions",
        "lineup_slot_eligibility",
        "scoring_rules",
        "schedule_rules",
    }

    assert required_tables.issubset(Base.metadata.tables)
    assert len(Base.metadata.tables) == 17


def test_lineup_slot_definition_fields() -> None:
    table = LineupSlotDefinition.__table__

    assert table.c.season_id.nullable is False
    assert table.c.provider_slot_code.type.length == 100
    assert table.c.canonical_slot_code.type.length == 100
    assert table.c.display_name.type.length == 120
    assert table.c.slot_order.nullable is False
    assert table.c.starter_count.nullable is False

    assert table.c.starter_count.server_default.arg == "0"
    assert table.c.is_starter.server_default.arg == "false"
    assert table.c.is_bench.server_default.arg == "false"
    assert table.c.is_injured_reserve.server_default.arg == "false"

    assert isinstance(table.c.metadata.type, JSONB)


def test_lineup_slot_definition_constraints() -> None:
    unique_names = constraint_names(
        "lineup_slot_definitions",
        UniqueConstraint,
    )
    check_names = constraint_names(
        "lineup_slot_definitions",
        CheckConstraint,
    )

    assert "uq_lineup_slot_definitions_season_provider_slot" in unique_names
    assert "ck_lineup_slot_definitions_slot_order_nonnegative" in check_names
    assert "ck_lineup_slot_definitions_starter_count_nonnegative" in check_names


def test_lineup_slot_eligibility_fields() -> None:
    table = LineupSlotEligibility.__table__

    assert table.c.lineup_slot_definition_id.nullable is False
    assert table.c.canonical_position.nullable is False
    assert table.c.canonical_position.type.length == 100
    assert table.c.created_at.nullable is False

    unique_names = constraint_names(
        "lineup_slot_eligibility",
        UniqueConstraint,
    )

    assert "uq_lineup_slot_eligibility_slot_position" in unique_names


def test_scoring_rule_fields() -> None:
    table = ScoringRule.__table__

    assert table.c.season_id.nullable is False
    assert table.c.provider_stat_code.type.length == 150
    assert table.c.canonical_stat_code.type.length == 150
    assert table.c.canonical_stat_code.nullable is True
    assert table.c.display_name.type.length == 200
    assert table.c.points_per_unit.nullable is False
    assert table.c.points_per_unit.type.precision == 12
    assert table.c.points_per_unit.type.scale == 6
    assert isinstance(table.c.metadata.type, JSONB)

    unique_names = constraint_names(
        "scoring_rules",
        UniqueConstraint,
    )

    assert "uq_scoring_rules_season_provider_stat" in unique_names


def test_schedule_rule_fields() -> None:
    table = ScheduleRule.__table__

    assert table.c.season_id.nullable is False
    assert table.c.regular_season_weeks.nullable is False
    assert table.c.playoff_week_start.nullable is True
    assert table.c.playoff_week_end.nullable is True

    assert table.c.has_divisions.server_default.arg == "false"
    assert table.c.has_median_game.server_default.arg == "false"
    assert table.c.has_doubleheaders.server_default.arg == "false"
    assert table.c.allows_ties.server_default.arg == "true"
    assert isinstance(table.c.metadata.type, JSONB)

    unique_names = constraint_names(
        "schedule_rules",
        UniqueConstraint,
    )

    assert "uq_schedule_rules_season" in unique_names


def test_rule_foreign_key_delete_rules() -> None:
    expected_foreign_keys = {
        (
            LineupSlotDefinition.__table__,
            "season_id",
        ): (
            "seasons.id",
            "CASCADE",
        ),
        (
            LineupSlotEligibility.__table__,
            "lineup_slot_definition_id",
        ): (
            "lineup_slot_definitions.id",
            "CASCADE",
        ),
        (
            ScoringRule.__table__,
            "season_id",
        ): (
            "seasons.id",
            "CASCADE",
        ),
        (
            ScheduleRule.__table__,
            "season_id",
        ): (
            "seasons.id",
            "CASCADE",
        ),
    }

    for (
        table,
        column_name,
    ), (
        target,
        delete_rule,
    ) in expected_foreign_keys.items():
        foreign_key = next(iter(table.c[column_name].foreign_keys))

        assert foreign_key.target_fullname == target
        assert foreign_key.ondelete == delete_rule


def test_dynamic_rule_relationships_configure() -> None:
    configure_mappers()

    assert Season.lineup_slot_definitions.property.back_populates == "season"
    assert LineupSlotDefinition.season.property.back_populates == "lineup_slot_definitions"
    assert LineupSlotDefinition.eligibility.property.back_populates == "lineup_slot_definition"
    assert LineupSlotEligibility.lineup_slot_definition.property.back_populates == "eligibility"
    assert Season.scoring_rules.property.back_populates == "season"
    assert ScoringRule.season.property.back_populates == "scoring_rules"
    assert Season.schedule_rule.property.back_populates == "season"
    assert ScheduleRule.season.property.back_populates == "schedule_rule"


def test_schedule_rule_is_one_to_one() -> None:
    assert Season.schedule_rule.property.uselist is False


def test_rule_column_counts() -> None:
    assert len(LineupSlotDefinition.__table__.columns) == 13
    assert len(LineupSlotEligibility.__table__.columns) == 4
    assert len(ScoringRule.__table__.columns) == 9
    assert len(ScheduleRule.__table__.columns) == 12
