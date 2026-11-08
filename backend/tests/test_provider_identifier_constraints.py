from sqlalchemy import UniqueConstraint

from app.db import model_registry  # noqa: F401
from app.db.models import (
    PlayerWeekScore,
    Team,
)


def unique_constraint_columns(
    model: type[Team] | type[PlayerWeekScore],
) -> dict[str, tuple[str, ...]]:
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
        and constraint.name is not None
    }


def test_team_has_legacy_and_canonical_uniqueness() -> None:
    constraints = unique_constraint_columns(Team)

    assert constraints["uq_teams_season_espn_team"] == (
        "season_id",
        "espn_team_id",
    )

    assert constraints["uq_teams_season_provider_team"] == (
        "season_id",
        "provider_team_id",
    )


def test_player_week_has_legacy_and_canonical_uniqueness() -> None:
    constraints = unique_constraint_columns(PlayerWeekScore)

    assert constraints["uq_player_week_scores_season_week_team_player"] == (
        "season_id",
        "week",
        "team_id",
        "espn_player_id",
    )

    assert constraints[("uq_player_week_scores_season_week_team_provider_player")] == (
        "season_id",
        "week",
        "team_id",
        "provider_player_id",
    )


def test_canonical_identifiers_remain_nullable_during_transition() -> None:
    assert Team.__table__.c.provider_team_id.nullable
    assert PlayerWeekScore.__table__.c.provider_player_id.nullable
