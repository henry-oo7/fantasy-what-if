from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    PlayerWeekScore,
    Team,
)
from app.domain.lineup import (
    PlayerWeekSnapshot,
    TeamWeekRoster,
)


def load_team_week_rosters(
    session: Session,
    *,
    season_id: int,
    through_week: int | None = None,
    weeks: tuple[int, ...] | None = None,
) -> tuple[TeamWeekRoster, ...]:
    """Load stored rosters using canonical internal identities."""

    statement = (
        select(
            PlayerWeekScore,
            Team,
        )
        .join(
            Team,
            PlayerWeekScore.team_id == Team.id,
        )
        .where(
            PlayerWeekScore.season_id == season_id,
            Team.season_id == season_id,
        )
        .order_by(
            PlayerWeekScore.week,
            Team.id,
            PlayerWeekScore.id,
        )
    )

    if through_week is not None:
        statement = statement.where(PlayerWeekScore.week <= through_week)

    if weeks is not None:
        normalized_weeks = tuple(sorted(set(weeks)))

        if any(not isinstance(week, int) or week < 1 for week in normalized_weeks):
            raise ValueError("weeks must contain positive integers.")

        statement = statement.where(PlayerWeekScore.week.in_(normalized_weeks))

    rows = session.execute(statement).all()

    grouped_players: dict[
        tuple[int, str, int],
        list[PlayerWeekSnapshot],
    ] = defaultdict(list)

    for player_score, team in rows:
        metadata = _normalize_metadata(player_score.raw_metadata)

        grouped_players[
            (
                team.id,
                team.name,
                player_score.week,
            )
        ].append(
            PlayerWeekSnapshot(
                player_id=player_score.id,
                player_name=player_score.player_name,
                pro_team=player_score.pro_team,
                default_position=(player_score.default_position),
                actual_lineup_slot=(player_score.actual_lineup_slot),
                eligible_lineup_slots=tuple(
                    str(slot) for slot in (player_score.eligible_lineup_slots or [])
                ),
                points=float(player_score.points),
                projected_points=(
                    float(player_score.projected_points)
                    if player_score.projected_points is not None
                    else None
                ),
                is_starter=player_score.is_starter,
                is_bench=player_score.is_bench,
                is_injured_reserve=(player_score.is_injured_reserve),
                is_nfl_bye=bool(
                    metadata.get(
                        "on_bye_week",
                        False,
                    )
                ),
            )
        )

    return tuple(
        TeamWeekRoster(
            team_id=team_id,
            team_name=team_name,
            week=week,
            players=tuple(players),
        )
        for (
            team_id,
            team_name,
            week,
        ), players in grouped_players.items()
    )


def _normalize_metadata(
    metadata: Any,
) -> dict[str, Any]:
    if isinstance(metadata, dict):
        return metadata

    return {}
