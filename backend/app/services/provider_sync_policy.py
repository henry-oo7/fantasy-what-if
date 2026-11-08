"""Provider-independent synchronization policy and validation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from decimal import Decimal

from app.providers.domain import (
    ProviderLeague,
    ProviderMatchup,
    ProviderWeeklyRoster,
)


class ProviderSyncValidationError(ValueError):
    """Canonical provider data failed synchronization validation."""


def select_completed_weeks(
    *,
    current_week: int,
    regular_season_weeks: int,
    full_sync: bool,
    explicit_from_week: int | None = None,
    explicit_through_week: int | None = None,
) -> tuple[int, ...]:
    """Select completed roster weeks for one synchronization."""

    if current_week < 1:
        raise ValueError("current_week must be positive.")

    if regular_season_weeks < 1:
        raise ValueError("regular_season_weeks must be positive.")

    if explicit_from_week is None and explicit_through_week is not None:
        explicit_from_week = explicit_through_week

    if explicit_through_week is None and explicit_from_week is not None:
        explicit_through_week = explicit_from_week

    if explicit_from_week is not None and explicit_from_week < 1:
        raise ValueError("explicit_from_week must be positive.")

    if explicit_through_week is not None and explicit_through_week < 1:
        raise ValueError("explicit_through_week must be positive.")

    if (
        explicit_from_week is not None
        and explicit_through_week is not None
        and explicit_from_week > explicit_through_week
    ):
        raise ValueError("explicit_from_week cannot exceed explicit_through_week.")

    last_completed_week = min(
        current_week - 1,
        regular_season_weeks,
    )

    if last_completed_week < 1:
        return ()

    if explicit_from_week is not None:
        assert explicit_through_week is not None

        through_week = min(
            explicit_through_week,
            last_completed_week,
        )

        if explicit_from_week > through_week:
            return ()

        return tuple(
            range(
                explicit_from_week,
                through_week + 1,
            )
        )

    if full_sync:
        return tuple(range(1, last_completed_week + 1))

    first_week = max(
        1,
        last_completed_week - 1,
    )

    return tuple(
        range(
            first_week,
            last_completed_week + 1,
        )
    )


def select_matchup_weeks(
    *,
    regular_season_weeks: int,
    explicit_from_week: int | None = None,
    explicit_through_week: int | None = None,
) -> tuple[int, ...]:
    """Select scheduled matchup weeks for synchronization."""

    if regular_season_weeks < 1:
        raise ValueError("regular_season_weeks must be positive.")

    from_week = explicit_from_week or 1
    through_week = explicit_through_week or regular_season_weeks

    if from_week < 1 or through_week < 1:
        raise ValueError("Requested matchup weeks must be positive.")

    if from_week > through_week:
        raise ValueError("Requested matchup week range is invalid.")

    through_week = min(
        through_week,
        regular_season_weeks,
    )

    if from_week > through_week:
        return ()

    return tuple(range(from_week, through_week + 1))


def validate_provider_league(
    league: ProviderLeague,
    *,
    expected_provider_league_id: str,
    expected_provider_season_id: str,
) -> None:
    """Validate league identity and canonical configuration."""

    if league.provider_league_id != expected_provider_league_id:
        raise ProviderSyncValidationError(
            "Provider league identity does not match the synchronized league."
        )

    if league.provider_season_id != expected_provider_season_id:
        raise ProviderSyncValidationError(
            "Provider season identity does not match the synchronized season."
        )

    if not league.teams:
        raise ProviderSyncValidationError("Provider league contains no teams.")

    team_ids = [team.provider_team_id for team in league.teams]

    duplicate_team_ids = _duplicates(team_ids)

    if duplicate_team_ids:
        raise ProviderSyncValidationError(
            "Provider league contains duplicate team IDs: " + ", ".join(duplicate_team_ids)
        )


def validate_provider_matchups(
    matchups: Iterable[ProviderMatchup],
    *,
    expected_team_ids: set[str],
    requested_weeks: tuple[int, ...],
    allows_doubleheaders: bool,
) -> None:
    """Validate canonical weekly matchup consistency."""

    requested_week_set = set(requested_weeks)
    appearances: Counter[tuple[int, str]] = Counter()

    for matchup in matchups:
        if matchup.week not in requested_week_set:
            raise ProviderSyncValidationError(
                f"Provider returned unrequested matchup week {matchup.week}."
            )

        referenced_ids = {
            matchup.home_provider_team_id,
        }

        if matchup.away_provider_team_id is not None:
            referenced_ids.add(matchup.away_provider_team_id)

        unknown_ids = referenced_ids - expected_team_ids

        if unknown_ids:
            raise ProviderSyncValidationError(
                "Provider matchup references unknown teams: " + ", ".join(sorted(unknown_ids))
            )

        appearances[
            (
                matchup.week,
                matchup.home_provider_team_id,
            )
        ] += 1

        if matchup.away_provider_team_id is not None:
            appearances[
                (
                    matchup.week,
                    matchup.away_provider_team_id,
                )
            ] += 1

        _validate_optional_decimal(
            matchup.home_score,
            field_name="home_score",
        )
        _validate_optional_decimal(
            matchup.away_score,
            field_name="away_score",
        )

    if not allows_doubleheaders:
        duplicate_appearances = sorted(
            (
                f"week {week}: {team_id}"
                for (
                    week,
                    team_id,
                ), count in appearances.items()
                if count > 1
            )
        )

        if duplicate_appearances:
            raise ProviderSyncValidationError(
                "Teams appear more than once without "
                "doubleheaders enabled: " + ", ".join(duplicate_appearances)
            )


def validate_provider_rosters(
    rosters: Iterable[ProviderWeeklyRoster],
    *,
    expected_team_ids: set[str],
    requested_weeks: tuple[int, ...],
) -> None:
    """Validate canonical weekly roster consistency."""

    requested_week_set = set(requested_weeks)
    seen_team_weeks: set[tuple[int, str]] = set()

    for roster in rosters:
        if roster.week not in requested_week_set:
            raise ProviderSyncValidationError(
                f"Provider returned unrequested roster week {roster.week}."
            )

        if roster.provider_team_id not in expected_team_ids:
            raise ProviderSyncValidationError(
                f"Provider roster references unknown team {roster.provider_team_id}."
            )

        team_week = (
            roster.week,
            roster.provider_team_id,
        )

        if team_week in seen_team_weeks:
            raise ProviderSyncValidationError(
                "Provider returned duplicate roster data for one team and week."
            )

        seen_team_weeks.add(team_week)

        player_ids = [player.provider_player_id for player in roster.players]

        duplicate_player_ids = _duplicates(player_ids)

        if duplicate_player_ids:
            raise ProviderSyncValidationError(
                "Provider roster contains duplicate players: " + ", ".join(duplicate_player_ids)
            )

        for player in roster.players:
            _validate_optional_decimal(
                player.points,
                field_name="points",
            )
            _validate_optional_decimal(
                player.projected_points,
                field_name="projected_points",
            )


def _duplicates(
    values: Iterable[str],
) -> tuple[str, ...]:
    counts = Counter(values)

    return tuple(sorted(value for value, count in counts.items() if count > 1))


def _validate_optional_decimal(
    value: Decimal | None,
    *,
    field_name: str,
) -> None:
    if value is None:
        return

    if not isinstance(value, Decimal):
        raise ProviderSyncValidationError(f"{field_name} must be a Decimal.")

    if not value.is_finite():
        raise ProviderSyncValidationError(f"{field_name} must be finite.")
