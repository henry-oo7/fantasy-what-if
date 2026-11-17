"""Resolve provider and administrator standings tiebreaker rules."""

from dataclasses import dataclass
from enum import StrEnum


class StandingsTiebreaker(StrEnum):
    """Standings tiebreakers supported by the application."""

    HEAD_TO_HEAD = "H2H_RECORD"
    POINTS_FOR = "TOTAL_POINTS_SCORED"


class StandingsTiebreakerSource(StrEnum):
    """Origin of an effective standings tiebreaker."""

    PROVIDER = "provider"
    ADMIN_FALLBACK = "admin_fallback"


@dataclass(frozen=True, slots=True)
class EffectiveStandingsTiebreaker:
    """One supported rule and the source that supplied it."""

    rule: StandingsTiebreaker
    source: StandingsTiebreakerSource


def normalize_standings_tiebreaker(
    value: str | None,
) -> StandingsTiebreaker | None:
    """Return a supported normalized rule or None."""

    if value is None:
        return None

    normalized = value.strip().upper()

    try:
        return StandingsTiebreaker(normalized)
    except ValueError:
        return None


def resolve_standings_tiebreaker(
    *,
    provider_rule: str | None,
    admin_fallback_rule: str | None,
) -> EffectiveStandingsTiebreaker | None:
    """Prefer a supported provider rule, then the administrator fallback."""

    normalized_provider_rule = normalize_standings_tiebreaker(
        provider_rule,
    )

    if normalized_provider_rule is not None:
        return EffectiveStandingsTiebreaker(
            rule=normalized_provider_rule,
            source=StandingsTiebreakerSource.PROVIDER,
        )

    normalized_fallback_rule = normalize_standings_tiebreaker(
        admin_fallback_rule,
    )

    if normalized_fallback_rule is None:
        return None

    return EffectiveStandingsTiebreaker(
        rule=normalized_fallback_rule,
        source=StandingsTiebreakerSource.ADMIN_FALLBACK,
    )
