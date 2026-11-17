from app.domain.standings_tiebreaker import (
    StandingsTiebreaker,
    StandingsTiebreakerSource,
    normalize_standings_tiebreaker,
    resolve_standings_tiebreaker,
)


def test_normalizes_supported_rules() -> None:
    assert normalize_standings_tiebreaker(" total_points_scored ") is StandingsTiebreaker.POINTS_FOR
    assert normalize_standings_tiebreaker("h2h_record") is StandingsTiebreaker.HEAD_TO_HEAD


def test_unsupported_rule_is_unavailable() -> None:
    assert normalize_standings_tiebreaker("COIN_FLIP") is None
    assert normalize_standings_tiebreaker(None) is None


def test_supported_provider_rule_wins_over_admin_fallback() -> None:
    effective = resolve_standings_tiebreaker(
        provider_rule="TOTAL_POINTS_SCORED",
        admin_fallback_rule="H2H_RECORD",
    )

    assert effective is not None
    assert effective.rule is StandingsTiebreaker.POINTS_FOR
    assert effective.source is StandingsTiebreakerSource.PROVIDER


def test_admin_fallback_is_used_when_provider_rule_is_missing() -> None:
    effective = resolve_standings_tiebreaker(
        provider_rule=None,
        admin_fallback_rule="H2H_RECORD",
    )

    assert effective is not None
    assert effective.rule is StandingsTiebreaker.HEAD_TO_HEAD
    assert effective.source is StandingsTiebreakerSource.ADMIN_FALLBACK


def test_admin_fallback_is_used_when_provider_rule_is_unsupported() -> None:
    effective = resolve_standings_tiebreaker(
        provider_rule="COIN_FLIP",
        admin_fallback_rule="TOTAL_POINTS_SCORED",
    )

    assert effective is not None
    assert effective.rule is StandingsTiebreaker.POINTS_FOR
    assert effective.source is StandingsTiebreakerSource.ADMIN_FALLBACK


def test_resolution_is_unavailable_without_supported_rule() -> None:
    assert (
        resolve_standings_tiebreaker(
            provider_rule=None,
            admin_fallback_rule=None,
        )
        is None
    )
