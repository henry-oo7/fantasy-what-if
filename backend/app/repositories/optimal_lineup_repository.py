"""Persist and reload optimal-lineup calculation results."""

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analytics.optimal_lineup import (
    LineupPlayer,
    LineupSubstitution,
    TeamOptimalLineupSummary,
    TeamWeekOptimalLineup,
)
from app.db.models import OptimalLineupResult

OPTIMAL_LINEUP_CALCULATION_VERSION = 1


def replace_optimal_lineup_results(
    session: Session,
    *,
    season_id: int,
    weeks: tuple[int, ...],
    results: tuple[
        TeamWeekOptimalLineup,
        ...,
    ],
) -> tuple[OptimalLineupResult, ...]:
    """Replace persisted calculations for selected weeks."""

    normalized_weeks = tuple(sorted(set(weeks)))

    if any(not isinstance(week, int) or week < 1 for week in normalized_weeks):
        raise ValueError("weeks must contain positive integers.")

    requested_week_set = set(normalized_weeks)

    seen_team_weeks: set[tuple[int, int]] = set()

    for result in results:
        if result.week not in requested_week_set:
            raise ValueError("Optimal-lineup result contains an unrequested week.")

        team_week = (
            result.team_id,
            result.week,
        )

        if team_week in seen_team_weeks:
            raise ValueError("Duplicate optimal-lineup result for one team and week.")

        seen_team_weeks.add(team_week)

    if normalized_weeks:
        session.execute(
            delete(OptimalLineupResult).where(
                OptimalLineupResult.season_id == season_id,
                OptimalLineupResult.week.in_(normalized_weeks),
            )
        )

    stored_results: list[OptimalLineupResult] = []

    for result in results:
        stored = OptimalLineupResult(
            season_id=season_id,
            team_id=result.team_id,
            week=result.week,
            team_name=result.team_name,
            actual_points=Decimal(str(result.actual_points)),
            optimal_points=Decimal(str(result.optimal_points)),
            missed_points=Decimal(str(result.missed_points)),
            efficiency_percentage=Decimal(str(result.efficiency_percentage)),
            actual_starter_count=(result.actual_starter_count),
            optimal_filled_slot_count=(result.optimal_filled_slot_count),
            unfilled_slots=list(result.unfilled_slots),
            actual_lineup=[asdict(player) for player in result.actual_lineup],
            optimal_lineup=[asdict(player) for player in result.optimal_lineup],
            substitutions=[asdict(substitution) for substitution in result.substitutions],
            calculation_version=(OPTIMAL_LINEUP_CALCULATION_VERSION),
        )

        session.add(stored)
        stored_results.append(stored)

    session.flush()

    return tuple(stored_results)


def load_optimal_lineup_week_results(
    session: Session,
    *,
    season_id: int,
    team_id: int | None = None,
) -> tuple[TeamWeekOptimalLineup, ...]:
    """Load persisted team-week domain results."""

    statement = (
        select(OptimalLineupResult)
        .where(
            OptimalLineupResult.season_id == season_id,
            OptimalLineupResult.calculation_version == OPTIMAL_LINEUP_CALCULATION_VERSION,
        )
        .order_by(
            OptimalLineupResult.team_id,
            OptimalLineupResult.week,
        )
    )

    if team_id is not None:
        statement = statement.where(OptimalLineupResult.team_id == team_id)

    stored_results = session.scalars(statement).all()

    return tuple(_to_domain_result(stored) for stored in stored_results)


def load_optimal_lineup_summaries(
    session: Session,
    *,
    season_id: int,
    team_id: int | None = None,
) -> tuple[TeamOptimalLineupSummary, ...]:
    """Aggregate persisted team-week results by team."""

    weekly_results = load_optimal_lineup_week_results(
        session,
        season_id=season_id,
        team_id=team_id,
    )

    results_by_team: dict[
        int,
        list[TeamWeekOptimalLineup],
    ] = {}

    for result in weekly_results:
        results_by_team.setdefault(
            result.team_id,
            [],
        ).append(result)

    summaries: list[TeamOptimalLineupSummary] = []

    for current_team_id, team_results in results_by_team.items():
        ordered_results = tuple(
            sorted(
                team_results,
                key=lambda result: result.week,
            )
        )

        actual_points = round(
            sum(result.actual_points for result in ordered_results),
            2,
        )

        optimal_points = round(
            sum(result.optimal_points for result in ordered_results),
            2,
        )

        missed_points = round(
            max(
                optimal_points - actual_points,
                0.0,
            ),
            2,
        )

        efficiency_percentage = round(
            100.0
            if optimal_points <= 0
            else min(
                actual_points / optimal_points * 100,
                100.0,
            ),
            2,
        )

        summaries.append(
            TeamOptimalLineupSummary(
                team_id=current_team_id,
                team_name=(ordered_results[0].team_name),
                weeks_played=len(ordered_results),
                actual_points=actual_points,
                optimal_points=optimal_points,
                missed_points=missed_points,
                efficiency_percentage=(efficiency_percentage),
                weekly_results=(ordered_results),
            )
        )

    return tuple(
        sorted(
            summaries,
            key=lambda summary: (
                -summary.efficiency_percentage,
                summary.missed_points,
                summary.team_name.casefold(),
                summary.team_id,
            ),
        )
    )


def _to_domain_result(
    stored: OptimalLineupResult,
) -> TeamWeekOptimalLineup:
    return TeamWeekOptimalLineup(
        team_id=stored.team_id,
        team_name=stored.team_name,
        week=stored.week,
        actual_points=float(stored.actual_points),
        optimal_points=float(stored.optimal_points),
        missed_points=float(stored.missed_points),
        efficiency_percentage=float(stored.efficiency_percentage),
        actual_starter_count=(stored.actual_starter_count),
        optimal_filled_slot_count=(stored.optimal_filled_slot_count),
        unfilled_slots=tuple(str(slot) for slot in stored.unfilled_slots),
        actual_lineup=tuple(_lineup_player(payload) for payload in stored.actual_lineup),
        optimal_lineup=tuple(_lineup_player(payload) for payload in stored.optimal_lineup),
        substitutions=tuple(_lineup_substitution(payload) for payload in stored.substitutions),
    )


def _lineup_player(
    payload: dict[str, Any],
) -> LineupPlayer:
    return LineupPlayer(
        lineup_slot=str(payload["lineup_slot"]),
        player_id=_optional_int(payload.get("player_id")),
        player_name=_optional_string(payload.get("player_name")),
        default_position=_optional_string(payload.get("default_position")),
        pro_team=_optional_string(payload.get("pro_team")),
        points=float(payload.get("points", 0.0)),
        actual_lineup_slot=_optional_string(payload.get("actual_lineup_slot")),
        was_actual_starter=bool(
            payload.get(
                "was_actual_starter",
                False,
            )
        ),
        is_unfilled=bool(
            payload.get(
                "is_unfilled",
                False,
            )
        ),
    )


def _lineup_substitution(
    payload: dict[str, Any],
) -> LineupSubstitution:
    return LineupSubstitution(
        lineup_slot=str(payload["lineup_slot"]),
        player_in_id=_optional_int(payload.get("player_in_id")),
        player_in_name=_optional_string(payload.get("player_in_name")),
        player_out_id=_optional_int(payload.get("player_out_id")),
        player_out_name=_optional_string(payload.get("player_out_name")),
        point_change=float(payload.get("point_change", 0.0)),
    )


def _optional_int(
    value: object,
) -> int | None:
    if value is None:
        return None

    return int(value)


def _optional_string(
    value: object,
) -> str | None:
    if value is None:
        return None

    return str(value)
