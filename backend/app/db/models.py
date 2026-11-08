from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin
from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
    SeasonStatus,
)

if TYPE_CHECKING:
    from app.db.platform_models import (
        Installation,
        ProviderConnection,
    )


class SyncStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class League(TimestampMixin, Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    installation_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "installations.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )
    provider: Mapped[FantasyProvider | None] = mapped_column(
        Enum(
            FantasyProvider,
            name="fantasy_provider",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
    )
    provider_league_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    sport: Mapped[FantasySport | None] = mapped_column(
        Enum(
            FantasySport,
            name="fantasy_sport",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
    )
    logo_url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    installation: Mapped[Installation | None] = relationship(
        back_populates="leagues",
        foreign_keys=[installation_id],
    )
    seasons: Mapped[list[Season]] = relationship(
        back_populates="league",
        cascade="all, delete-orphan",
    )
    franchises: Mapped[list[Franchise]] = relationship(
        back_populates="league",
        cascade="all, delete-orphan",
    )


class Franchise(TimestampMixin, Base):
    __tablename__ = "franchises"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    league_id: Mapped[int] = mapped_column(
        ForeignKey(
            "leagues.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    logo_url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    league: Mapped[League] = relationship(
        back_populates="franchises",
    )
    season_teams: Mapped[list[Team]] = relationship(
        back_populates="franchise",
    )


class Season(TimestampMixin, Base):
    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint(
            "league_id",
            "year",
            name="uq_seasons_league_year",
        ),
        sa.Index(
            "uq_seasons_one_active_per_league",
            "league_id",
            unique=True,
            postgresql_where=sa.text("status = 'active'::season_status"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    current_week: Mapped[int] = mapped_column(Integer, nullable=False)
    regular_season_weeks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    provider_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "provider_connections.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    provider_season_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    status: Mapped[SeasonStatus | None] = mapped_column(
        Enum(
            SeasonStatus,
            name="season_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
        index=True,
    )
    team_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    playoff_week_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    playoff_week_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    scoring_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    supports_head_to_head: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    supports_weekly_matchups: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    supports_lineup_history: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    supports_optimal_lineups: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    supports_schedule_swap: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    supports_all_play: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    supports_league_average: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    supports_team_logos: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    configuration_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    league: Mapped[League] = relationship(back_populates="seasons")
    provider_connection: Mapped[ProviderConnection | None] = relationship(
        back_populates="seasons",
    )
    teams: Mapped[list[Team]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
    )
    matchups: Mapped[list[Matchup]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
    )
    player_week_scores: Mapped[list[PlayerWeekScore]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
    )
    sync_runs: Mapped[list[SyncRun]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
    )
    lineup_slot_definitions: Mapped[list[LineupSlotDefinition]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
    )
    scoring_rules: Mapped[list[ScoringRule]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
    )
    schedule_rule: Mapped[ScheduleRule | None] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Team(TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint(
            "season_id",
            "provider_team_id",
            name="uq_teams_season_provider_team",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_team_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    franchise_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "franchises.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    owner_display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    logo_url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ties: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points_for: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=0,
    )
    points_against: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=0,
    )

    season: Mapped[Season] = relationship(back_populates="teams")
    franchise: Mapped[Franchise | None] = relationship(
        back_populates="season_teams",
    )
    home_matchups: Mapped[list[Matchup]] = relationship(
        back_populates="home_team",
        foreign_keys="Matchup.home_team_id",
    )
    away_matchups: Mapped[list[Matchup]] = relationship(
        back_populates="away_team",
        foreign_keys="Matchup.away_team_id",
    )
    player_week_scores: Mapped[list[PlayerWeekScore]] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
    )


class Matchup(TimestampMixin, Base):
    __tablename__ = "matchups"
    __table_args__ = (
        UniqueConstraint(
            "season_id",
            "week",
            "home_team_id",
            name="uq_matchups_season_week_home_team",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    away_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
    )

    home_score: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    away_score: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    is_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    is_bye: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    season: Mapped[Season] = relationship(back_populates="matchups")
    home_team: Mapped[Team] = relationship(
        back_populates="home_matchups",
        foreign_keys=[home_team_id],
    )
    away_team: Mapped[Team | None] = relationship(
        back_populates="away_matchups",
        foreign_keys=[away_team_id],
    )


class PlayerWeekScore(TimestampMixin, Base):
    __tablename__ = "player_week_scores"
    __table_args__ = (
        UniqueConstraint(
            "season_id",
            "week",
            "team_id",
            "provider_player_id",
            name=("uq_player_week_scores_season_week_team_provider_player"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    provider_player_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    provider_position: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pro_team: Mapped[str | None] = mapped_column(String(20), nullable=True)
    default_position: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    actual_lineup_slot: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    eligible_lineup_slots: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    points: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=0,
    )
    projected_points: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    was_on_roster: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    is_starter: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_bench: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_injured_reserve: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    season: Mapped[Season] = relationship(
        back_populates="player_week_scores",
    )
    team: Mapped[Team] = relationship(
        back_populates="player_week_scores",
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "provider_connections.id",
            name=("fk_sync_runs_provider_connection_id_provider_connections"),
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    provider: Mapped[FantasyProvider | None] = mapped_column(
        Enum(
            FantasyProvider,
            name="fantasy_provider",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=True,
        index=True,
    )
    provider_request_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    analytics_rebuilt: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[SyncStatus] = mapped_column(
        Enum(
            SyncStatus,
            name="sync_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=SyncStatus.RUNNING,
    )
    is_full_sync: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    requested_from_week: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    requested_through_week: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    teams_upserted: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    matchups_upserted: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    player_week_scores_upserted: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    safe_error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    season: Mapped[Season | None] = relationship(
        back_populates="sync_runs",
    )
    provider_connection: Mapped[ProviderConnection | None] = relationship(
        back_populates="sync_runs",
    )


class LineupSlotDefinition(TimestampMixin, Base):
    __tablename__ = "lineup_slot_definitions"
    __table_args__ = (
        UniqueConstraint(
            "season_id",
            "provider_slot_code",
            name=("uq_lineup_slot_definitions_season_provider_slot"),
        ),
        sa.CheckConstraint(
            "slot_order >= 0",
            name=("ck_lineup_slot_definitions_slot_order_nonnegative"),
        ),
        sa.CheckConstraint(
            "starter_count >= 0",
            name=("ck_lineup_slot_definitions_starter_count_nonnegative"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey(
            "seasons.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    provider_slot_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    canonical_slot_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )
    slot_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    starter_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    is_starter: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    is_bench: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    is_injured_reserve: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    metadata_payload: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    season: Mapped[Season] = relationship(
        back_populates="lineup_slot_definitions",
    )
    eligibility: Mapped[list[LineupSlotEligibility]] = relationship(
        back_populates="lineup_slot_definition",
        cascade="all, delete-orphan",
    )


class LineupSlotEligibility(Base):
    __tablename__ = "lineup_slot_eligibility"
    __table_args__ = (
        UniqueConstraint(
            "lineup_slot_definition_id",
            "canonical_position",
            name=("uq_lineup_slot_eligibility_slot_position"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    lineup_slot_definition_id: Mapped[int] = mapped_column(
        ForeignKey(
            "lineup_slot_definitions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    canonical_position: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    lineup_slot_definition: Mapped[LineupSlotDefinition] = relationship(
        back_populates="eligibility",
    )


class ScoringRule(TimestampMixin, Base):
    __tablename__ = "scoring_rules"
    __table_args__ = (
        UniqueConstraint(
            "season_id",
            "provider_stat_code",
            name=("uq_scoring_rules_season_provider_stat"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey(
            "seasons.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    provider_stat_code: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )
    canonical_stat_code: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )
    display_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    points_per_unit: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
    )
    metadata_payload: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    season: Mapped[Season] = relationship(
        back_populates="scoring_rules",
    )


class ScheduleRule(TimestampMixin, Base):
    __tablename__ = "schedule_rules"
    __table_args__ = (
        UniqueConstraint(
            "season_id",
            name="uq_schedule_rules_season",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey(
            "seasons.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    regular_season_weeks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    playoff_week_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    playoff_week_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    has_divisions: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    has_median_game: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    has_doubleheaders: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    allows_ties: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    metadata_payload: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    season: Mapped[Season] = relationship(
        back_populates="schedule_rule",
    )
