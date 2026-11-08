from decimal import Decimal

import pytest

from app.db.platform_enums import FantasySport
from app.providers.domain import (
    ProviderCapabilities,
    ProviderLeague,
    ProviderMatchup,
    ProviderPlayerWeek,
    ProviderScheduleRules,
    ProviderTeam,
    ProviderWeeklyRoster,
)
from app.services.provider_sync_policy import (
    ProviderSyncValidationError,
    select_completed_weeks,
    select_matchup_weeks,
    validate_provider_league,
    validate_provider_matchups,
    validate_provider_rosters,
)


def make_team(
    provider_team_id: str,
) -> ProviderTeam:
    return ProviderTeam(
        provider_team_id=provider_team_id,
        name=f"Team {provider_team_id}",
        wins=0,
        losses=0,
        ties=0,
        points_for=Decimal("0"),
        points_against=Decimal("0"),
    )


def make_league() -> ProviderLeague:
    return ProviderLeague(
        provider_league_id="529250970",
        provider_season_id="2026",
        name="Jimmy and Friends",
        sport=FantasySport.FOOTBALL,
        season_year=2026,
        current_week=9,
        regular_season_weeks=12,
        playoff_week_start=None,
        playoff_week_end=None,
        scoring_type="head_to_head_points",
        logo_url=None,
        teams=(
            make_team("1"),
            make_team("2"),
        ),
        lineup_slots=(),
        scoring_rules=(),
        schedule_rules=ProviderScheduleRules(
            regular_season_weeks=12,
            playoff_week_start=None,
            playoff_week_end=None,
            has_divisions=False,
            has_median_game=False,
            has_doubleheaders=False,
            allows_ties=True,
        ),
        capabilities=ProviderCapabilities(
            supports_head_to_head=True,
            supports_weekly_matchups=True,
            supports_lineup_history=True,
            supports_optimal_lineups=False,
            supports_schedule_swap=True,
            supports_all_play=True,
            supports_league_average=True,
            supports_team_logos=False,
        ),
    )


def make_player(
    provider_player_id: str,
) -> ProviderPlayerWeek:
    return ProviderPlayerWeek(
        provider_player_id=provider_player_id,
        player_name=f"Player {provider_player_id}",
        pro_team=None,
        provider_position="WR",
        canonical_position="WR",
        actual_lineup_slot="WR",
        eligible_lineup_slots=("WR",),
        points=Decimal("10"),
        projected_points=None,
        is_starter=True,
        is_bench=False,
        is_injured_reserve=False,
        is_on_roster=True,
    )


def test_incremental_weeks_are_latest_two_completed() -> None:
    assert select_completed_weeks(
        current_week=9,
        regular_season_weeks=12,
        full_sync=False,
    ) == (
        7,
        8,
    )


def test_full_sync_selects_all_completed_weeks() -> None:
    assert select_completed_weeks(
        current_week=5,
        regular_season_weeks=12,
        full_sync=True,
    ) == (
        1,
        2,
        3,
        4,
    )


def test_no_completed_weeks_returns_empty() -> None:
    assert (
        select_completed_weeks(
            current_week=1,
            regular_season_weeks=12,
            full_sync=False,
        )
        == ()
    )


def test_explicit_week_range_is_clamped() -> None:
    assert select_completed_weeks(
        current_week=9,
        regular_season_weeks=12,
        full_sync=False,
        explicit_from_week=6,
        explicit_through_week=10,
    ) == (
        6,
        7,
        8,
    )


def test_matchup_weeks_default_to_regular_season() -> None:
    assert select_matchup_weeks(
        regular_season_weeks=3,
    ) == (
        1,
        2,
        3,
    )


def test_invalid_week_range_is_rejected() -> None:
    with pytest.raises(ValueError):
        select_completed_weeks(
            current_week=9,
            regular_season_weeks=12,
            full_sync=False,
            explicit_from_week=8,
            explicit_through_week=7,
        )


def test_league_identity_is_validated() -> None:
    validate_provider_league(
        make_league(),
        expected_provider_league_id="529250970",
        expected_provider_season_id="2026",
    )

    with pytest.raises(ProviderSyncValidationError):
        validate_provider_league(
            make_league(),
            expected_provider_league_id="different",
            expected_provider_season_id="2026",
        )


def test_matchup_validation_accepts_known_teams() -> None:
    validate_provider_matchups(
        (
            ProviderMatchup(
                week=8,
                home_provider_team_id="1",
                away_provider_team_id="2",
                home_score=Decimal("100"),
                away_score=Decimal("90"),
                is_complete=True,
                is_bye=False,
            ),
        ),
        expected_team_ids={
            "1",
            "2",
        },
        requested_weeks=(8,),
        allows_doubleheaders=False,
    )


def test_matchups_reject_unrequested_week() -> None:
    with pytest.raises(ProviderSyncValidationError):
        validate_provider_matchups(
            (
                ProviderMatchup(
                    week=7,
                    home_provider_team_id="1",
                    away_provider_team_id="2",
                    home_score=Decimal("100"),
                    away_score=Decimal("90"),
                    is_complete=True,
                    is_bye=False,
                ),
            ),
            expected_team_ids={
                "1",
                "2",
            },
            requested_weeks=(8,),
            allows_doubleheaders=False,
        )


def test_matchups_reject_unknown_team() -> None:
    with pytest.raises(ProviderSyncValidationError):
        validate_provider_matchups(
            (
                ProviderMatchup(
                    week=8,
                    home_provider_team_id="1",
                    away_provider_team_id="99",
                    home_score=Decimal("100"),
                    away_score=Decimal("90"),
                    is_complete=True,
                    is_bye=False,
                ),
            ),
            expected_team_ids={
                "1",
                "2",
            },
            requested_weeks=(8,),
            allows_doubleheaders=False,
        )


def test_matchups_reject_double_appearance() -> None:
    with pytest.raises(ProviderSyncValidationError):
        validate_provider_matchups(
            (
                ProviderMatchup(
                    week=8,
                    home_provider_team_id="1",
                    away_provider_team_id="2",
                    home_score=Decimal("100"),
                    away_score=Decimal("90"),
                    is_complete=True,
                    is_bye=False,
                ),
                ProviderMatchup(
                    week=8,
                    home_provider_team_id="1",
                    away_provider_team_id=None,
                    home_score=None,
                    away_score=None,
                    is_complete=True,
                    is_bye=True,
                ),
            ),
            expected_team_ids={
                "1",
                "2",
            },
            requested_weeks=(8,),
            allows_doubleheaders=False,
        )


def test_roster_validation_accepts_known_team() -> None:
    validate_provider_rosters(
        (
            ProviderWeeklyRoster(
                provider_team_id="1",
                week=8,
                players=(make_player("10"),),
            ),
        ),
        expected_team_ids={
            "1",
            "2",
        },
        requested_weeks=(8,),
    )


def test_rosters_reject_unknown_team() -> None:
    with pytest.raises(ProviderSyncValidationError):
        validate_provider_rosters(
            (
                ProviderWeeklyRoster(
                    provider_team_id="99",
                    week=8,
                    players=(make_player("10"),),
                ),
            ),
            expected_team_ids={
                "1",
                "2",
            },
            requested_weeks=(8,),
        )


def test_rosters_reject_duplicate_team_week() -> None:
    roster = ProviderWeeklyRoster(
        provider_team_id="1",
        week=8,
        players=(make_player("10"),),
    )

    with pytest.raises(ProviderSyncValidationError):
        validate_provider_rosters(
            (
                roster,
                roster,
            ),
            expected_team_ids={
                "1",
                "2",
            },
            requested_weeks=(8,),
        )
