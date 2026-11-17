"""Read and update season standings tiebreaker configuration."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ScheduleRule
from app.domain.standings_tiebreaker import (
    EffectiveStandingsTiebreaker,
    StandingsTiebreaker,
    normalize_standings_tiebreaker,
    resolve_standings_tiebreaker,
)


class StandingsTiebreakerConfigurationError(RuntimeError):
    """Standings tiebreaker configuration could not be loaded or updated."""


@dataclass(frozen=True, slots=True)
class StandingsTiebreakerConfiguration:
    """Provider, fallback, and effective rules for one season."""

    season_id: int
    provider_rule: str | None
    provider_rule_supported: bool
    admin_fallback_rule: StandingsTiebreaker | None
    effective: EffectiveStandingsTiebreaker | None


def get_standings_tiebreaker_configuration(
    session: Session,
    *,
    season_id: int,
) -> StandingsTiebreakerConfiguration:
    """Load standings tiebreaker configuration for one season."""

    schedule_rule = session.scalar(
        select(ScheduleRule).where(
            ScheduleRule.season_id == season_id,
        )
    )

    if schedule_rule is None:
        raise StandingsTiebreakerConfigurationError(
            "Schedule configuration is unavailable for the selected season."
        )

    provider_rule = schedule_rule.playoff_seed_tie_rule
    normalized_provider_rule = normalize_standings_tiebreaker(
        provider_rule,
    )
    admin_fallback_rule = normalize_standings_tiebreaker(
        schedule_rule.playoff_seed_tie_rule_fallback,
    )

    return StandingsTiebreakerConfiguration(
        season_id=season_id,
        provider_rule=provider_rule,
        provider_rule_supported=normalized_provider_rule is not None,
        admin_fallback_rule=admin_fallback_rule,
        effective=resolve_standings_tiebreaker(
            provider_rule=provider_rule,
            admin_fallback_rule=(schedule_rule.playoff_seed_tie_rule_fallback),
        ),
    )


def set_standings_tiebreaker_fallback(
    session: Session,
    *,
    season_id: int,
    fallback_rule: StandingsTiebreaker,
) -> StandingsTiebreakerConfiguration:
    """Store an administrator fallback without changing provider data."""

    schedule_rule = session.scalar(
        select(ScheduleRule).where(
            ScheduleRule.season_id == season_id,
        )
    )

    if schedule_rule is None:
        raise StandingsTiebreakerConfigurationError(
            "Schedule configuration is unavailable for the selected season."
        )

    schedule_rule.playoff_seed_tie_rule_fallback = fallback_rule.value
    session.flush()

    return get_standings_tiebreaker_configuration(
        session,
        season_id=season_id,
    )
