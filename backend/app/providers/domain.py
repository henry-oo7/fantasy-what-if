"""Provider-independent immutable domain objects."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
)

Metadata = Mapping[str, Any]


def immutable_mapping(
    values: Mapping[str, Any] | None = None,
) -> Metadata:
    """Return a shallow, read-only copy of provider metadata."""

    return MappingProxyType(dict(values or {}))


@dataclass(frozen=True, slots=True)
class ProviderCredentials:
    """Provider-specific credentials used during construction."""

    values: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "values",
            MappingProxyType(dict(self.values)),
        )


@dataclass(frozen=True, slots=True)
class ProviderValidationResult:
    """Result of validating provider configuration."""

    is_valid: bool
    provider: FantasyProvider
    safe_message: str
    metadata: Metadata = field(
        default_factory=immutable_mapping,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            immutable_mapping(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Analytics capabilities supported by one provider season."""

    supports_head_to_head: bool
    supports_weekly_matchups: bool
    supports_lineup_history: bool
    supports_optimal_lineups: bool
    supports_schedule_swap: bool
    supports_all_play: bool
    supports_league_average: bool
    supports_team_logos: bool


@dataclass(frozen=True, slots=True)
class ProviderLeagueReference:
    """A league available during provider discovery."""

    provider_league_id: str
    provider_season_id: str
    name: str
    sport: FantasySport
    season_year: int
    logo_url: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_league_id.strip():
            raise ValueError("provider_league_id must not be empty.")

        if not self.provider_season_id.strip():
            raise ValueError("provider_season_id must not be empty.")

        if not self.name.strip():
            raise ValueError("League name must not be empty.")

        if self.season_year < 1:
            raise ValueError("season_year must be positive.")


@dataclass(frozen=True, slots=True)
class ProviderTeam:
    """Provider-independent team data for one season."""

    provider_team_id: str
    name: str
    wins: int
    losses: int
    ties: int
    points_for: Decimal
    points_against: Decimal
    abbreviation: str | None = None
    owner_display_name: str | None = None
    logo_url: str | None = None
    is_active: bool = True
    metadata: Metadata = field(
        default_factory=immutable_mapping,
    )

    def __post_init__(self) -> None:
        if not self.provider_team_id.strip():
            raise ValueError("provider_team_id must not be empty.")

        if not self.name.strip():
            raise ValueError("Team name must not be empty.")

        for field_name in (
            "wins",
            "losses",
            "ties",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must not be negative.")

        for field_name in (
            "points_for",
            "points_against",
        ):
            value = getattr(self, field_name)

            if not isinstance(value, Decimal):
                raise TypeError(f"{field_name} must be a Decimal.")

            if not value.is_finite():
                raise ValueError(f"{field_name} must be finite.")

        object.__setattr__(
            self,
            "metadata",
            immutable_mapping(self.metadata),
        )


def validate_decimal(
    *,
    field_name: str,
    value: Decimal | None,
) -> None:
    """Require optional canonical point values to be finite Decimals."""

    if value is None:
        return

    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal.")

    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite.")


@dataclass(frozen=True, slots=True)
class ProviderMatchup:
    """One provider matchup during a scoring week."""

    week: int
    home_provider_team_id: str
    away_provider_team_id: str | None
    home_score: Decimal | None
    away_score: Decimal | None
    is_complete: bool
    is_bye: bool
    metadata: Metadata = field(
        default_factory=immutable_mapping,
    )

    def __post_init__(self) -> None:
        if self.week < 1:
            raise ValueError("Matchup week must be positive.")

        if not self.home_provider_team_id.strip():
            raise ValueError("home_provider_team_id must not be empty.")

        if self.away_provider_team_id is not None and not self.away_provider_team_id.strip():
            raise ValueError("away_provider_team_id must not be empty.")

        if self.is_bye and self.away_provider_team_id is not None:
            raise ValueError("A bye matchup cannot have an away team.")

        if not self.is_bye and self.away_provider_team_id is None:
            raise ValueError("A non-bye matchup requires an away team.")

        if self.away_provider_team_id == self.home_provider_team_id:
            raise ValueError("A team cannot play itself.")

        validate_decimal(
            field_name="home_score",
            value=self.home_score,
        )
        validate_decimal(
            field_name="away_score",
            value=self.away_score,
        )

        if (
            self.is_complete
            and not self.is_bye
            and (self.home_score is None or self.away_score is None)
        ):
            raise ValueError("Completed non-bye matchups require both scores.")

        object.__setattr__(
            self,
            "metadata",
            immutable_mapping(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ProviderPlayerWeek:
    """One player's provider result for one team-week roster."""

    provider_player_id: str
    player_name: str
    pro_team: str | None
    provider_position: str
    canonical_position: str
    actual_lineup_slot: str
    eligible_lineup_slots: tuple[str, ...]
    points: Decimal
    projected_points: Decimal | None
    is_starter: bool
    is_bench: bool
    is_injured_reserve: bool
    is_on_roster: bool
    metadata: Metadata = field(
        default_factory=immutable_mapping,
    )

    def __post_init__(self) -> None:
        required_values = {
            "provider_player_id": self.provider_player_id,
            "player_name": self.player_name,
            "provider_position": self.provider_position,
            "canonical_position": self.canonical_position,
            "actual_lineup_slot": self.actual_lineup_slot,
        }

        for field_name, value in required_values.items():
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty.")

        normalized_slots = tuple(
            str(slot).strip() for slot in self.eligible_lineup_slots if str(slot).strip()
        )

        object.__setattr__(
            self,
            "eligible_lineup_slots",
            normalized_slots,
        )

        validate_decimal(
            field_name="points",
            value=self.points,
        )
        validate_decimal(
            field_name="projected_points",
            value=self.projected_points,
        )

        classifications = sum(
            (
                self.is_starter,
                self.is_bench,
                self.is_injured_reserve,
            )
        )

        if classifications > 1:
            raise ValueError("Starter, bench, and injured-reserve classifications cannot conflict.")

        object.__setattr__(
            self,
            "metadata",
            immutable_mapping(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ProviderWeeklyRoster:
    """One provider team roster for one scoring week."""

    provider_team_id: str
    week: int
    players: tuple[ProviderPlayerWeek, ...]

    def __post_init__(self) -> None:
        if not self.provider_team_id.strip():
            raise ValueError("provider_team_id must not be empty.")

        if self.week < 1:
            raise ValueError("Roster week must be positive.")

        player_ids = [player.provider_player_id for player in self.players]

        if len(player_ids) != len(set(player_ids)):
            raise ValueError("A player appears more than once in the team-week roster.")


@dataclass(frozen=True, slots=True)
class ProviderLineupSlot:
    """One season lineup-slot definition."""

    provider_slot_code: str
    canonical_slot_code: str
    display_name: str
    order: int
    starter_count: int
    is_starter: bool
    is_bench: bool
    is_injured_reserve: bool
    eligible_positions: tuple[str, ...]
    metadata: Metadata = field(
        default_factory=immutable_mapping,
    )

    def __post_init__(self) -> None:
        required_values = {
            "provider_slot_code": self.provider_slot_code,
            "canonical_slot_code": self.canonical_slot_code,
            "display_name": self.display_name,
        }

        for field_name, value in required_values.items():
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty.")

        if self.order < 0:
            raise ValueError("Lineup-slot order must not be negative.")

        if self.starter_count < 0:
            raise ValueError("starter_count must not be negative.")

        normalized_positions = tuple(
            str(position).strip() for position in self.eligible_positions if str(position).strip()
        )

        object.__setattr__(
            self,
            "eligible_positions",
            normalized_positions,
        )
        object.__setattr__(
            self,
            "metadata",
            immutable_mapping(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ProviderScoringRule:
    """One provider scoring rule for a season."""

    provider_stat_code: str
    canonical_stat_code: str | None
    display_name: str
    points_per_unit: Decimal
    metadata: Metadata = field(
        default_factory=immutable_mapping,
    )

    def __post_init__(self) -> None:
        if not self.provider_stat_code.strip():
            raise ValueError("provider_stat_code must not be empty.")

        if not self.display_name.strip():
            raise ValueError("Scoring-rule display name must not be empty.")

        validate_decimal(
            field_name="points_per_unit",
            value=self.points_per_unit,
        )

        object.__setattr__(
            self,
            "metadata",
            immutable_mapping(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ProviderScheduleRules:
    """Provider-independent schedule settings for one season."""

    regular_season_weeks: int
    playoff_week_start: int | None
    playoff_week_end: int | None
    has_divisions: bool
    has_median_game: bool
    has_doubleheaders: bool
    allows_ties: bool
    metadata: Metadata = field(
        default_factory=immutable_mapping,
    )

    def __post_init__(self) -> None:
        if self.regular_season_weeks < 1:
            raise ValueError("regular_season_weeks must be positive.")

        for field_name in (
            "playoff_week_start",
            "playoff_week_end",
        ):
            value = getattr(self, field_name)

            if value is not None and value < 1:
                raise ValueError(f"{field_name} must be positive.")

        if (
            self.playoff_week_start is not None
            and self.playoff_week_end is not None
            and self.playoff_week_end < self.playoff_week_start
        ):
            raise ValueError("playoff_week_end cannot precede playoff_week_start.")

        object.__setattr__(
            self,
            "metadata",
            immutable_mapping(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ProviderLeague:
    """Complete canonical provider configuration for one season."""

    provider_league_id: str
    provider_season_id: str
    name: str
    sport: FantasySport
    season_year: int
    current_week: int
    regular_season_weeks: int
    playoff_week_start: int | None
    playoff_week_end: int | None
    scoring_type: str | None
    logo_url: str | None
    teams: tuple[ProviderTeam, ...]
    lineup_slots: tuple[ProviderLineupSlot, ...]
    scoring_rules: tuple[ProviderScoringRule, ...]
    schedule_rules: ProviderScheduleRules
    capabilities: ProviderCapabilities
    metadata: Metadata = field(
        default_factory=immutable_mapping,
    )

    def __post_init__(self) -> None:
        if not self.provider_league_id.strip():
            raise ValueError("provider_league_id must not be empty.")

        if not self.provider_season_id.strip():
            raise ValueError("provider_season_id must not be empty.")

        if not self.name.strip():
            raise ValueError("League name must not be empty.")

        if self.season_year < 1:
            raise ValueError("season_year must be positive.")

        if self.current_week < 1:
            raise ValueError("current_week must be positive.")

        if self.regular_season_weeks < 1:
            raise ValueError("regular_season_weeks must be positive.")

        if self.schedule_rules.regular_season_weeks != self.regular_season_weeks:
            raise ValueError("League and schedule regular-season lengths must match.")

        team_ids = [team.provider_team_id for team in self.teams]

        if len(team_ids) != len(set(team_ids)):
            raise ValueError("Provider league contains duplicate team IDs.")

        slot_codes = [slot.provider_slot_code for slot in self.lineup_slots]

        if len(slot_codes) != len(set(slot_codes)):
            raise ValueError("Provider league contains duplicate lineup-slot codes.")

        scoring_codes = [rule.provider_stat_code for rule in self.scoring_rules]

        if len(scoring_codes) != len(set(scoring_codes)):
            raise ValueError("Provider league contains duplicate scoring-rule codes.")

        object.__setattr__(
            self,
            "metadata",
            immutable_mapping(self.metadata),
        )
