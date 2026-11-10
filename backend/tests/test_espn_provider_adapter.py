from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from app.providers.espn.adapter import (
    create_matchups_with_byes,
    create_provider_matchup,
    create_provider_player_week,
    create_provider_team,
    create_provider_weekly_roster,
    decimal_from_provider,
)


@dataclass
class FakeTeam:
    team_id: int
    team_name: str
    wins: int = 5
    losses: int = 3
    ties: int = 0
    points_for: float = 1012.34
    points_against: float = 998.76
    team_abbrev: str | None = "EVN"
    owner: object | None = "League Manager"
    logo_url: str | None = None


@dataclass
class FakePlayer:
    playerId: int
    name: str
    position: str = "WR"
    lineupSlot: str = "WR"
    eligibleSlots: list[str] = field(
        default_factory=lambda: [
            "WR",
            "RB/WR/TE",
            "BE",
        ]
    )
    points: float = 18.5
    projected_points: float | None = 16.75
    proTeam: str | None = "LAR"
    injuryStatus: str | None = "ACTIVE"
    active_status: str | None = "ACTIVE"
    on_bye_week: bool = False


@dataclass
class FakeMatchup:
    home_team: FakeTeam
    away_team: FakeTeam
    home_score: float = 124.5
    away_score: float = 117.25
    home_lineup: list[FakePlayer] = field(default_factory=list)
    away_lineup: list[FakePlayer] = field(default_factory=list)


def test_decimal_conversion_uses_string_representation() -> None:
    assert decimal_from_provider(
        18.5,
        field_name="points",
    ) == Decimal("18.5")


@pytest.mark.parametrize(
    "value",
    [
        float("inf"),
        float("-inf"),
        float("nan"),
    ],
)
def test_decimal_conversion_rejects_nonfinite_values(
    value: float,
) -> None:
    with pytest.raises(ValueError):
        decimal_from_provider(
            value,
            field_name="points",
        )


def test_team_conversion_uses_string_identifier() -> None:
    team = create_provider_team(
        FakeTeam(
            team_id=2,
            team_name="STAIRWAY TO EVANS",
        )
    )

    assert team.provider_team_id == "2"
    assert team.name == "STAIRWAY TO EVANS"
    assert team.abbreviation == "EVN"
    assert team.owner_display_name == "League Manager"
    assert team.points_for == Decimal("1012.34")
    assert team.points_against == Decimal("998.76")
    assert team.metadata["provider"] == "espn"


def test_team_owner_dictionary_uses_display_name() -> None:
    team = create_provider_team(
        FakeTeam(
            team_id=2,
            team_name="STAIRWAY TO EVANS",
            owner={
                "displayName": "espn97319207",
                "firstName": "Jimmy",
                "lastName": "HA",
                "notificationSettings": [
                    {
                        "enabled": True,
                        "type": "WAIVERS",
                    }
                ],
            },
        )
    )

    assert team.owner_display_name == "espn97319207"


def test_team_owner_dictionary_uses_name_fallback() -> None:
    team = create_provider_team(
        FakeTeam(
            team_id=2,
            team_name="STAIRWAY TO EVANS",
            owner={
                "firstName": "Jimmy",
                "lastName": "HA",
            },
        )
    )

    assert team.owner_display_name == "Jimmy HA"


def test_team_owner_list_is_normalized() -> None:
    team = create_provider_team(
        FakeTeam(
            team_id=2,
            team_name="STAIRWAY TO EVANS",
            owner=[
                {"displayName": "Owner One"},
                {"displayName": "Owner Two"},
            ],
        )
    )

    assert team.owner_display_name == "Owner One, Owner Two"


def test_team_owner_value_is_bounded() -> None:
    team = create_provider_team(
        FakeTeam(
            team_id=2,
            team_name="STAIRWAY TO EVANS",
            owner={
                "displayName": "x" * 400,
            },
        )
    )

    assert team.owner_display_name is not None
    assert len(team.owner_display_name) == 255


def test_completed_matchup_conversion() -> None:
    matchup = create_provider_matchup(
        FakeMatchup(
            home_team=FakeTeam(
                team_id=2,
                team_name="Home",
            ),
            away_team=FakeTeam(
                team_id=3,
                team_name="Away",
            ),
        ),
        week=8,
        is_complete=True,
    )

    assert matchup.week == 8
    assert matchup.home_provider_team_id == "2"
    assert matchup.away_provider_team_id == "3"
    assert matchup.home_score == Decimal("124.5")
    assert matchup.away_score == Decimal("117.25")
    assert matchup.is_complete is True
    assert matchup.is_bye is False


def test_incomplete_matchup_omits_scores() -> None:
    matchup = create_provider_matchup(
        FakeMatchup(
            home_team=FakeTeam(
                team_id=2,
                team_name="Home",
            ),
            away_team=FakeTeam(
                team_id=3,
                team_name="Away",
            ),
        ),
        week=9,
        is_complete=False,
    )

    assert matchup.home_score is None
    assert matchup.away_score is None


def test_player_conversion_classifies_starter() -> None:
    player = create_provider_player_week(
        FakePlayer(
            playerId=4426515,
            name="Puka Nacua",
        )
    )

    assert player.provider_player_id == "4426515"
    assert player.provider_position == "WR"
    assert player.canonical_position == "WR"
    assert player.actual_lineup_slot == "WR"
    assert player.points == Decimal("18.5")
    assert player.projected_points == Decimal("16.75")
    assert player.is_starter is True
    assert player.is_bench is False
    assert player.is_injured_reserve is False
    assert player.is_on_roster is True
    assert player.metadata["canonical_lineup_slot"] == "WR"


def test_player_conversion_classifies_bench() -> None:
    player = create_provider_player_week(
        FakePlayer(
            playerId=4426515,
            name="Puka Nacua",
            lineupSlot="BE",
        )
    )

    assert player.is_starter is False
    assert player.is_bench is True
    assert player.is_injured_reserve is False


def test_player_conversion_classifies_injured_reserve() -> None:
    player = create_provider_player_week(
        FakePlayer(
            playerId=4426515,
            name="Puka Nacua",
            lineupSlot="IR",
        )
    )

    assert player.is_starter is False
    assert player.is_bench is False
    assert player.is_injured_reserve is True


def test_weekly_roster_conversion() -> None:
    roster = create_provider_weekly_roster(
        provider_team_id="2",
        week=8,
        lineup=[
            FakePlayer(
                playerId=4426515,
                name="Puka Nacua",
            ),
            FakePlayer(
                playerId=4427366,
                name="Breece Hall",
                position="RB",
                lineupSlot="RB",
            ),
        ],
    )

    assert roster.provider_team_id == "2"
    assert roster.week == 8
    assert len(roster.players) == 2


def test_weekly_roster_rejects_duplicate_players() -> None:
    with pytest.raises(ValueError):
        create_provider_weekly_roster(
            provider_team_id="2",
            week=8,
            lineup=[
                FakePlayer(
                    playerId=4426515,
                    name="Puka Nacua",
                ),
                FakePlayer(
                    playerId=4426515,
                    name="Puka Nacua",
                ),
            ],
        )


def test_matchup_conversion_adds_explicit_bye() -> None:
    matchups = create_matchups_with_byes(
        week=8,
        matchups=[
            FakeMatchup(
                home_team=FakeTeam(
                    team_id=2,
                    team_name="Home",
                ),
                away_team=FakeTeam(
                    team_id=3,
                    team_name="Away",
                ),
            )
        ],
        provider_team_ids=(
            "2",
            "3",
            "4",
        ),
        is_complete=True,
    )

    assert len(matchups) == 2

    bye = next(matchup for matchup in matchups if matchup.is_bye)

    assert bye.home_provider_team_id == "4"
    assert bye.away_provider_team_id is None


def test_matchups_reject_duplicate_team_appearance() -> None:
    with pytest.raises(ValueError):
        create_matchups_with_byes(
            week=8,
            matchups=[
                FakeMatchup(
                    home_team=FakeTeam(
                        team_id=2,
                        team_name="Home",
                    ),
                    away_team=FakeTeam(
                        team_id=3,
                        team_name="Away",
                    ),
                ),
                FakeMatchup(
                    home_team=FakeTeam(
                        team_id=2,
                        team_name="Home Again",
                    ),
                    away_team=FakeTeam(
                        team_id=4,
                        team_name="Other",
                    ),
                ),
            ],
            provider_team_ids=(
                "2",
                "3",
                "4",
            ),
            is_complete=True,
        )


def test_matchups_reject_unknown_team() -> None:
    with pytest.raises(ValueError):
        create_matchups_with_byes(
            week=8,
            matchups=[
                FakeMatchup(
                    home_team=FakeTeam(
                        team_id=2,
                        team_name="Home",
                    ),
                    away_team=FakeTeam(
                        team_id=99,
                        team_name="Unknown",
                    ),
                )
            ],
            provider_team_ids=(
                "2",
                "3",
            ),
            is_complete=True,
        )
