"""Convert raw ESPN objects into canonical provider domain objects."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.providers.domain import (
    ProviderMatchup,
    ProviderPlayerWeek,
    ProviderTeam,
    ProviderWeeklyRoster,
    immutable_mapping,
)
from app.providers.espn.mappings import (
    canonical_lineup_slot,
    canonical_position,
    is_bench_slot,
    is_injured_reserve_slot,
    is_starter_slot,
    normalize_eligible_slots,
    normalize_espn_code,
)
from app.providers.espn.protocols import (
    EspnMatchup,
    EspnPlayer,
    EspnTeam,
)


def decimal_from_provider(
    value: object,
    *,
    field_name: str,
) -> Decimal:
    """Convert an ESPN numeric value into a finite Decimal."""

    try:
        converted = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field_name} is not a valid decimal value.") from exc

    if not converted.is_finite():
        raise ValueError(f"{field_name} must be finite.")

    return converted


def optional_decimal_from_provider(
    value: object | None,
    *,
    field_name: str,
) -> Decimal | None:
    """Convert an optional ESPN numeric value."""

    if value is None:
        return None

    return decimal_from_provider(
        value,
        field_name=field_name,
    )


def optional_text(
    value: object | None,
) -> str | None:
    """Normalize an optional ESPN text value."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None


def first_optional_attribute(
    source: object,
    names: tuple[str, ...],
) -> object | None:
    """Return the first nonempty optional ESPN attribute."""

    for name in names:
        value = getattr(source, name, None)

        if value is not None:
            return value

    return None


def owner_display_name(
    value: object | None,
) -> str | None:
    """Extract a bounded display name from ESPN owner data."""

    if value is None:
        return None

    if isinstance(value, dict):
        display_name = optional_text(value.get("displayName"))

        if display_name is not None:
            return display_name[:255]

        name_parts = tuple(
            normalized
            for field_name in (
                "firstName",
                "lastName",
            )
            if (normalized := optional_text(value.get(field_name))) is not None
        )

        if name_parts:
            return " ".join(name_parts)[:255]

        return None

    if isinstance(value, (list, tuple)):
        names = tuple(name for owner in value if (name := owner_display_name(owner)) is not None)

        if not names:
            return None

        return ", ".join(names)[:255]

    normalized = optional_text(value)

    if normalized is None:
        return None

    return normalized[:255]


def create_provider_team(
    team: EspnTeam,
) -> ProviderTeam:
    """Convert one ESPN team into a canonical provider team."""

    provider_team_id = str(team.team_id).strip()

    owner_value = first_optional_attribute(
        team,
        (
            "owner",
            "owners",
            "manager",
        ),
    )

    normalized_owner_display_name = owner_display_name(owner_value)

    abbreviation = optional_text(
        first_optional_attribute(
            team,
            (
                "team_abbrev",
                "abbreviation",
            ),
        )
    )

    logo_url = optional_text(
        first_optional_attribute(
            team,
            (
                "logo_url",
                "logo",
            ),
        )
    )

    return ProviderTeam(
        provider_team_id=provider_team_id,
        name=str(team.team_name).strip(),
        abbreviation=abbreviation,
        owner_display_name=(normalized_owner_display_name),
        logo_url=logo_url,
        wins=int(team.wins),
        losses=int(team.losses),
        ties=int(team.ties),
        points_for=decimal_from_provider(
            team.points_for,
            field_name="points_for",
        ),
        points_against=decimal_from_provider(
            team.points_against,
            field_name="points_against",
        ),
        is_active=True,
        metadata=immutable_mapping(
            {
                "provider": "espn",
            }
        ),
    )


def create_provider_matchup(
    matchup: EspnMatchup,
    *,
    week: int,
    is_complete: bool,
) -> ProviderMatchup:
    """Convert one ESPN box score into a canonical matchup."""

    home_team_id = str(matchup.home_team.team_id).strip()
    away_team_id = str(matchup.away_team.team_id).strip()

    return ProviderMatchup(
        week=week,
        home_provider_team_id=home_team_id,
        away_provider_team_id=away_team_id,
        home_score=(
            decimal_from_provider(
                matchup.home_score,
                field_name="home_score",
            )
            if is_complete
            else None
        ),
        away_score=(
            decimal_from_provider(
                matchup.away_score,
                field_name="away_score",
            )
            if is_complete
            else None
        ),
        is_complete=is_complete,
        is_bye=False,
        metadata=immutable_mapping(
            {
                "provider": "espn",
            }
        ),
    )


def create_provider_bye_matchup(
    *,
    week: int,
    provider_team_id: str,
    is_complete: bool,
) -> ProviderMatchup:
    """Create a canonical fantasy bye record."""

    return ProviderMatchup(
        week=week,
        home_provider_team_id=provider_team_id,
        away_provider_team_id=None,
        home_score=None,
        away_score=None,
        is_complete=is_complete,
        is_bye=True,
        metadata=immutable_mapping(
            {
                "provider": "espn",
                "generated_bye": True,
            }
        ),
    )


def create_provider_player_week(
    player: EspnPlayer,
) -> ProviderPlayerWeek:
    """Convert one ESPN lineup player into canonical player data."""

    provider_slot = normalize_espn_code(player.lineupSlot)
    provider_position = normalize_espn_code(player.position)

    injury_status = optional_text(getattr(player, "injuryStatus", None))
    active_status = optional_text(getattr(player, "active_status", None))
    is_nfl_bye = bool(getattr(player, "on_bye_week", False))

    return ProviderPlayerWeek(
        provider_player_id=str(player.playerId).strip(),
        player_name=str(player.name).strip(),
        pro_team=optional_text(getattr(player, "proTeam", None)),
        provider_position=provider_position,
        canonical_position=canonical_position(provider_position),
        actual_lineup_slot=canonical_lineup_slot(provider_slot),
        eligible_lineup_slots=normalize_eligible_slots(getattr(player, "eligibleSlots", ())),
        points=decimal_from_provider(
            player.points,
            field_name="points",
        ),
        projected_points=optional_decimal_from_provider(
            getattr(
                player,
                "projected_points",
                None,
            ),
            field_name="projected_points",
        ),
        is_starter=is_starter_slot(provider_slot),
        is_bench=is_bench_slot(provider_slot),
        is_injured_reserve=(is_injured_reserve_slot(provider_slot)),
        is_on_roster=True,
        metadata=immutable_mapping(
            {
                "provider": "espn",
                "injury_status": injury_status,
                "active_status": active_status,
                "is_nfl_bye": is_nfl_bye,
                "canonical_lineup_slot": (canonical_lineup_slot(provider_slot)),
            }
        ),
    )


def create_provider_weekly_roster(
    *,
    provider_team_id: str,
    week: int,
    lineup: Iterable[EspnPlayer],
) -> ProviderWeeklyRoster:
    """Convert one ESPN lineup into a canonical weekly roster."""

    players = tuple(create_provider_player_week(player) for player in lineup)

    return ProviderWeeklyRoster(
        provider_team_id=provider_team_id,
        week=week,
        players=players,
    )


def create_matchups_with_byes(
    *,
    week: int,
    matchups: Iterable[EspnMatchup],
    provider_team_ids: Iterable[str],
    is_complete: bool,
) -> tuple[ProviderMatchup, ...]:
    """Convert ESPN matchups and add explicit fantasy bye records."""

    canonical_matchups: list[ProviderMatchup] = []
    seen_team_ids: set[str] = set()

    for matchup in matchups:
        canonical = create_provider_matchup(
            matchup,
            week=week,
            is_complete=is_complete,
        )

        referenced_team_ids = {
            canonical.home_provider_team_id,
            canonical.away_provider_team_id,
        }

        for provider_team_id in referenced_team_ids:
            if provider_team_id is None:
                continue

            if provider_team_id in seen_team_ids:
                raise ValueError(
                    f"Team {provider_team_id} appears more than once in ESPN week {week}."
                )

            seen_team_ids.add(provider_team_id)

        canonical_matchups.append(canonical)

    expected_team_ids = {str(provider_team_id).strip() for provider_team_id in provider_team_ids}

    unknown_team_ids = seen_team_ids - expected_team_ids

    if unknown_team_ids:
        unknown = ", ".join(sorted(unknown_team_ids))

        raise ValueError(f"ESPN matchup referenced unknown teams: {unknown}.")

    missing_team_ids = expected_team_ids - seen_team_ids

    for provider_team_id in sorted(missing_team_ids):
        canonical_matchups.append(
            create_provider_bye_matchup(
                week=week,
                provider_team_id=provider_team_id,
                is_complete=is_complete,
            )
        )

    return tuple(canonical_matchups)
