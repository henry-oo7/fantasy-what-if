from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from app.db.platform_enums import FantasyProvider
from app.providers.base import ProviderAdapter
from app.providers.domain import ProviderCredentials
from app.providers.errors import (
    ProviderNotFoundError,
    ProviderResponseError,
)
from app.providers.espn.provider import (
    EspnProvider,
    register_espn_provider,
)
from app.providers.registry import ProviderRegistry


@dataclass
class FakeSettings:
    name: str = "Jimmy and Friends"
    reg_season_count: int = 12
    position_slot_counts: dict[str, int] = field(
        default_factory=lambda: {
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "D/ST": 1,
            "K": 1,
            "BE": 7,
            "IR": 1,
            "RB/WR/TE": 1,
        }
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
    team_abbrev: str | None = None
    owner: str | None = None
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


@dataclass
class FakeMatchup:
    home_team: FakeTeam
    away_team: FakeTeam
    home_score: float = 124.5
    away_score: float = 117.25
    home_lineup: list[FakePlayer] = field(default_factory=list)
    away_lineup: list[FakePlayer] = field(default_factory=list)


class FakeLeague:
    league_id = 529250970
    year = 2026
    current_week = 9
    settings = FakeSettings()

    def __init__(self) -> None:
        self.teams = [
            FakeTeam(
                team_id=2,
                team_name="STAIRWAY TO EVANS",
            ),
            FakeTeam(
                team_id=3,
                team_name="Warren Peace",
            ),
            FakeTeam(
                team_id=4,
                team_name="Kamara Sutra",
            ),
        ]

    def box_scores(
        self,
        week: int,
    ) -> list[FakeMatchup]:
        if week == 8:
            return [
                FakeMatchup(
                    home_team=self.teams[0],
                    away_team=self.teams[1],
                    home_lineup=[
                        FakePlayer(
                            playerId=4426515,
                            name="Puka Nacua",
                        )
                    ],
                    away_lineup=[
                        FakePlayer(
                            playerId=4427366,
                            name="Breece Hall",
                            position="RB",
                            lineupSlot="RB",
                        )
                    ],
                )
            ]

        if week == 9:
            return [
                FakeMatchup(
                    home_team=self.teams[0],
                    away_team=self.teams[1],
                )
            ]

        return []


def credentials() -> ProviderCredentials:
    return ProviderCredentials(
        values={
            "league_id": "529250970",
            "season": "2026",
        }
    )


def provider() -> EspnProvider:
    return EspnProvider(
        credentials(),
        client_factory=lambda values: FakeLeague(),
    )


def test_provider_satisfies_protocol() -> None:
    assert isinstance(provider(), ProviderAdapter)


def test_validation_succeeds() -> None:
    result = provider().validate_configuration()

    assert result.is_valid is True
    assert result.provider is FantasyProvider.ESPN
    assert result.metadata["private_league"] is False


def test_validation_returns_safe_failure() -> None:
    adapter = EspnProvider(
        credentials(),
        client_factory=lambda values: _raise_runtime_error(),
    )

    result = adapter.validate_configuration()

    assert result.is_valid is False
    assert "could not load" in result.safe_message
    assert result.metadata["retryable"] is False


def _raise_runtime_error() -> FakeLeague:
    raise RuntimeError("private provider response")


def test_explicit_league_discovery() -> None:
    leagues = provider().discover_leagues()

    assert len(leagues) == 1
    assert leagues[0].provider_league_id == "529250970"
    assert leagues[0].provider_season_id == "2026"
    assert leagues[0].name == "Jimmy and Friends"


def test_fetch_league_returns_canonical_data() -> None:
    league = provider().fetch_league(
        provider_league_id="529250970",
        provider_season_id="2026",
    )

    assert league.provider_league_id == "529250970"
    assert league.provider_season_id == "2026"
    assert league.current_week == 9
    assert league.regular_season_weeks == 12
    assert len(league.teams) == 3
    assert league.teams[0].provider_team_id == "2"
    assert [
        (
            slot.provider_slot_code,
            slot.canonical_slot_code,
            slot.starter_count,
            slot.is_starter,
        )
        for slot in league.lineup_slots
    ] == [
        ("QB", "QB", 1, True),
        ("RB", "RB", 2, True),
        ("WR", "WR", 2, True),
        ("TE", "TE", 1, True),
        ("D/ST", "DST", 1, True),
        ("K", "K", 1, True),
        ("BE", "BENCH", 0, False),
        ("IR", "IR", 0, False),
        ("RB/WR/TE", "FLEX", 1, True),
    ]
    assert league.scoring_rules == ()
    assert league.capabilities.supports_optimal_lineups is True


def test_fetch_league_preserves_double_flex_count() -> None:
    fake_league = FakeLeague()
    fake_league.settings = FakeSettings()
    fake_league.settings.position_slot_counts["RB/WR/TE"] = 2

    adapter = EspnProvider(
        credentials(),
        client_factory=lambda _: fake_league,
    )

    league = adapter.fetch_league(
        provider_league_id="529250970",
        provider_season_id="2026",
    )

    flex = next(slot for slot in league.lineup_slots if slot.canonical_slot_code == "FLEX")

    assert flex.provider_slot_code == "RB/WR/TE"
    assert flex.starter_count == 2
    assert flex.is_starter is True
    assert flex.eligible_positions == (
        "RB",
        "WR",
        "TE",
    )


def test_fetch_rejects_different_identity() -> None:
    with pytest.raises(ProviderNotFoundError):
        provider().fetch_league(
            provider_league_id="different",
            provider_season_id="2026",
        )


def test_fetch_matchups_respects_requested_weeks() -> None:
    matchups = provider().fetch_matchups(
        provider_league_id="529250970",
        provider_season_id="2026",
        weeks=(8,),
    )

    assert len(matchups) == 2

    played = next(matchup for matchup in matchups if not matchup.is_bye)
    bye = next(matchup for matchup in matchups if matchup.is_bye)

    assert played.week == 8
    assert played.home_score == Decimal("124.5")
    assert played.away_score == Decimal("117.25")
    assert bye.home_provider_team_id == "4"


def test_incomplete_week_omits_scores() -> None:
    matchups = provider().fetch_matchups(
        provider_league_id="529250970",
        provider_season_id="2026",
        weeks=(9,),
    )

    played = next(matchup for matchup in matchups if not matchup.is_bye)

    assert played.is_complete is False
    assert played.home_score is None
    assert played.away_score is None


def test_completed_empty_week_is_rejected() -> None:
    with pytest.raises(ProviderResponseError):
        provider().fetch_matchups(
            provider_league_id="529250970",
            provider_season_id="2026",
            weeks=(7,),
        )


def test_fetch_rosters_returns_requested_week() -> None:
    rosters = provider().fetch_weekly_rosters(
        provider_league_id="529250970",
        provider_season_id="2026",
        weeks=(8,),
    )

    assert len(rosters) == 2
    assert {roster.provider_team_id for roster in rosters} == {
        "2",
        "3",
    }

    home = next(roster for roster in rosters if roster.provider_team_id == "2")

    assert home.week == 8
    assert home.players[0].provider_player_id == "4426515"


@pytest.mark.parametrize(
    "weeks",
    [
        (0,),
        (-1,),
    ],
)
def test_invalid_requested_weeks_are_rejected(
    weeks: tuple[int, ...],
) -> None:
    with pytest.raises(ProviderResponseError):
        provider().fetch_matchups(
            provider_league_id="529250970",
            provider_season_id="2026",
            weeks=weeks,
        )


def test_client_is_loaded_lazily_and_cached() -> None:
    calls = 0

    def factory(
        values: ProviderCredentials,
    ) -> FakeLeague:
        nonlocal calls
        calls += 1
        return FakeLeague()

    adapter = EspnProvider(
        credentials(),
        client_factory=factory,
    )

    assert calls == 0

    adapter.discover_leagues()
    adapter.fetch_league(
        provider_league_id="529250970",
        provider_season_id="2026",
    )

    assert calls == 1


def test_registry_registration_is_opt_in() -> None:
    registry = ProviderRegistry()

    register_espn_provider(registry)

    assert registry.is_registered(FantasyProvider.ESPN)

    adapter = registry.create(
        FantasyProvider.ESPN,
        credentials(),
    )

    assert isinstance(adapter, ProviderAdapter)
