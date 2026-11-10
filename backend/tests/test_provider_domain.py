from dataclasses import FrozenInstanceError
from decimal import Decimal
from math import inf
from types import MappingProxyType

import pytest

from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
)
from app.providers.domain import (
    ProviderCapabilities,
    ProviderCredentials,
    ProviderLeague,
    ProviderLeagueReference,
    ProviderLineupSlot,
    ProviderMatchup,
    ProviderPlayerWeek,
    ProviderScheduleRules,
    ProviderScoringRule,
    ProviderTeam,
    ProviderValidationResult,
    ProviderWeeklyRoster,
)


def make_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        supports_head_to_head=True,
        supports_weekly_matchups=True,
        supports_lineup_history=True,
        supports_optimal_lineups=True,
        supports_schedule_swap=True,
        supports_all_play=True,
        supports_league_average=True,
        supports_team_logos=False,
    )


def make_team(
    provider_team_id: str = "2",
) -> ProviderTeam:
    return ProviderTeam(
        provider_team_id=provider_team_id,
        name="STAIRWAY TO EVANS",
        wins=5,
        losses=3,
        ties=0,
        points_for=Decimal("1012.34"),
        points_against=Decimal("998.76"),
        metadata={"source": "fixture"},
    )


def make_player(
    provider_player_id: str = "4426515",
) -> ProviderPlayerWeek:
    return ProviderPlayerWeek(
        provider_player_id=provider_player_id,
        player_name="Puka Nacua",
        pro_team="LAR",
        provider_position="WR",
        canonical_position="WR",
        actual_lineup_slot="WR",
        eligible_lineup_slots=(
            "WR",
            "FLEX",
            "BENCH",
        ),
        points=Decimal("18.50"),
        projected_points=Decimal("16.75"),
        is_starter=True,
        is_bench=False,
        is_injured_reserve=False,
        is_on_roster=True,
        metadata={"injury_status": "ACTIVE"},
    )


def make_schedule_rules() -> ProviderScheduleRules:
    return ProviderScheduleRules(
        regular_season_weeks=12,
        playoff_week_start=13,
        playoff_week_end=16,
        has_divisions=False,
        has_median_game=False,
        has_doubleheaders=False,
        allows_ties=True,
    )


def test_credentials_are_copied_and_read_only() -> None:
    source = {
        "league_id": "529250970",
    }

    credentials = ProviderCredentials(
        values=source,
    )

    source["league_id"] = "changed"

    assert credentials.values["league_id"] == "529250970"
    assert isinstance(
        credentials.values,
        MappingProxyType,
    )

    with pytest.raises(TypeError):
        credentials.values["league_id"] = "changed"


def test_validation_metadata_is_read_only() -> None:
    result = ProviderValidationResult(
        is_valid=True,
        provider=FantasyProvider.ESPN,
        safe_message="Configuration is valid.",
        metadata={"operation": "validation"},
    )

    assert isinstance(
        result.metadata,
        MappingProxyType,
    )

    with pytest.raises(TypeError):
        result.metadata["operation"] = "changed"


def test_provider_domain_objects_are_frozen() -> None:
    team = make_team()

    with pytest.raises(FrozenInstanceError):
        team.name = "Changed"


@pytest.mark.parametrize(
    (
        "provider_league_id",
        "provider_season_id",
        "name",
        "season_year",
    ),
    [
        ("", "2026", "League", 2026),
        ("529250970", "", "League", 2026),
        ("529250970", "2026", "", 2026),
        ("529250970", "2026", "League", 0),
    ],
)
def test_invalid_league_references_are_rejected(
    provider_league_id: str,
    provider_season_id: str,
    name: str,
    season_year: int,
) -> None:
    with pytest.raises(ValueError):
        ProviderLeagueReference(
            provider_league_id=provider_league_id,
            provider_season_id=provider_season_id,
            name=name,
            sport=FantasySport.FOOTBALL,
            season_year=season_year,
        )


def test_team_requires_decimal_points() -> None:
    with pytest.raises(TypeError):
        ProviderTeam(
            provider_team_id="2",
            name="Team",
            wins=0,
            losses=0,
            ties=0,
            points_for=100.5,
            points_against=Decimal("99.50"),
        )


def test_team_rejects_nonfinite_points() -> None:
    with pytest.raises(ValueError):
        ProviderTeam(
            provider_team_id="2",
            name="Team",
            wins=0,
            losses=0,
            ties=0,
            points_for=Decimal(str(inf)),
            points_against=Decimal("99.50"),
        )


def test_completed_matchup_requires_scores() -> None:
    with pytest.raises(ValueError):
        ProviderMatchup(
            week=8,
            home_provider_team_id="2",
            away_provider_team_id="3",
            home_score=None,
            away_score=None,
            is_complete=True,
            is_bye=False,
        )


def test_bye_matchup_cannot_have_away_team() -> None:
    with pytest.raises(ValueError):
        ProviderMatchup(
            week=8,
            home_provider_team_id="2",
            away_provider_team_id="3",
            home_score=None,
            away_score=None,
            is_complete=False,
            is_bye=True,
        )


def test_player_slots_are_normalized() -> None:
    player = ProviderPlayerWeek(
        provider_player_id="4426515",
        player_name="Puka Nacua",
        pro_team="LAR",
        provider_position="WR",
        canonical_position="WR",
        actual_lineup_slot="WR",
        eligible_lineup_slots=(
            " WR ",
            "",
            " FLEX ",
        ),
        points=Decimal("18.50"),
        projected_points=None,
        is_starter=True,
        is_bench=False,
        is_injured_reserve=False,
        is_on_roster=True,
    )

    assert player.eligible_lineup_slots == (
        "WR",
        "FLEX",
    )


def test_player_classifications_cannot_conflict() -> None:
    with pytest.raises(ValueError):
        ProviderPlayerWeek(
            provider_player_id="4426515",
            player_name="Puka Nacua",
            pro_team="LAR",
            provider_position="WR",
            canonical_position="WR",
            actual_lineup_slot="WR",
            eligible_lineup_slots=("WR",),
            points=Decimal("18.50"),
            projected_points=None,
            is_starter=True,
            is_bench=True,
            is_injured_reserve=False,
            is_on_roster=True,
        )


def test_roster_rejects_duplicate_players() -> None:
    player = make_player()

    with pytest.raises(ValueError):
        ProviderWeeklyRoster(
            provider_team_id="2",
            week=8,
            players=(
                player,
                player,
            ),
        )


def test_lineup_slot_rejects_negative_counts() -> None:
    with pytest.raises(ValueError):
        ProviderLineupSlot(
            provider_slot_code="RB",
            canonical_slot_code="RB",
            display_name="Running Back",
            order=1,
            starter_count=-1,
            is_starter=True,
            is_bench=False,
            is_injured_reserve=False,
            eligible_positions=("RB",),
        )


def test_scoring_rule_requires_decimal_value() -> None:
    with pytest.raises(TypeError):
        ProviderScoringRule(
            provider_stat_code="REC",
            canonical_stat_code="reception",
            display_name="Reception",
            points_per_unit=0.5,
        )


def test_schedule_rejects_invalid_playoff_range() -> None:
    with pytest.raises(ValueError):
        ProviderScheduleRules(
            regular_season_weeks=12,
            playoff_week_start=16,
            playoff_week_end=13,
            has_divisions=False,
            has_median_game=False,
            has_doubleheaders=False,
            allows_ties=True,
        )


def test_league_rejects_duplicate_team_ids() -> None:
    team = make_team()

    with pytest.raises(ValueError):
        ProviderLeague(
            provider_league_id="529250970",
            provider_season_id="2026",
            name="Jimmy and Friends",
            sport=FantasySport.FOOTBALL,
            season_year=2026,
            current_week=9,
            regular_season_weeks=12,
            playoff_week_start=13,
            playoff_week_end=16,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=(team, team),
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=make_schedule_rules(),
            capabilities=make_capabilities(),
        )


def test_league_requires_matching_schedule_length() -> None:
    schedule = ProviderScheduleRules(
        regular_season_weeks=14,
        playoff_week_start=15,
        playoff_week_end=17,
        has_divisions=False,
        has_median_game=False,
        has_doubleheaders=False,
        allows_ties=True,
    )

    with pytest.raises(ValueError):
        ProviderLeague(
            provider_league_id="529250970",
            provider_season_id="2026",
            name="Jimmy and Friends",
            sport=FantasySport.FOOTBALL,
            season_year=2026,
            current_week=9,
            regular_season_weeks=12,
            playoff_week_start=13,
            playoff_week_end=16,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=(make_team(),),
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=schedule,
            capabilities=make_capabilities(),
        )
