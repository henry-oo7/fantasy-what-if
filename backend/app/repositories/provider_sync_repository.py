"""Persistence boundary for provider-independent synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    Matchup,
    PlayerWeekScore,
    ScheduleRule,
    Season,
    SyncRun,
    SyncStatus,
    Team,
)
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import (
    ProviderConnection,
)
from app.providers.domain import (
    ProviderLeague,
    ProviderMatchup,
    ProviderScheduleRules,
    ProviderTeam,
    ProviderWeeklyRoster,
)


class ProviderSyncContextNotFoundError(LookupError):
    """The requested internal season does not exist."""


class ProviderSyncContextError(ValueError):
    """The season is missing required canonical configuration."""


@dataclass(frozen=True, slots=True)
class ProviderSyncContext:
    """Canonical database context required for one synchronization."""

    season: Season
    league: League
    provider_connection: ProviderConnection

    @property
    def season_id(self) -> int:
        return self.season.id

    @property
    def league_id(self) -> int:
        return self.league.id

    @property
    def provider_connection_id(self) -> int:
        return self.provider_connection.id

    @property
    def provider(self) -> FantasyProvider:
        return self.provider_connection.provider

    @property
    def provider_league_id(self) -> str:
        provider_league_id = self.league.provider_league_id

        if provider_league_id is None:
            raise ProviderSyncContextError("The league does not have a canonical provider ID.")

        return provider_league_id

    @property
    def provider_season_id(self) -> str:
        provider_season_id = self.season.provider_season_id

        if provider_season_id is None:
            raise ProviderSyncContextError("The season does not have a canonical provider ID.")

        return provider_season_id


class ProviderSyncRepository:
    """Persist provider-neutral synchronization state."""

    def __init__(
        self,
        session: Session,
    ) -> None:
        self.session = session

    def load_context(
        self,
        *,
        season_id: int,
    ) -> ProviderSyncContext:
        """Load one season and validate its provider configuration."""

        if season_id < 1:
            raise ValueError("season_id must be positive.")

        season = self.session.get(
            Season,
            season_id,
        )

        if season is None:
            raise ProviderSyncContextNotFoundError(f"Season {season_id} was not found.")

        league = self.session.get(
            League,
            season.league_id,
        )

        if league is None:
            raise ProviderSyncContextError("The season references an unavailable league.")

        if season.provider_connection_id is None:
            raise ProviderSyncContextError("The season does not have a provider connection.")

        provider_connection = self.session.get(
            ProviderConnection,
            season.provider_connection_id,
        )

        if provider_connection is None:
            raise ProviderSyncContextError(
                "The season references an unavailable provider connection."
            )

        if not provider_connection.is_active:
            raise ProviderSyncContextError("The season provider connection is inactive.")

        if league.provider is None:
            raise ProviderSyncContextError("The league does not have a canonical provider.")

        if league.provider is not provider_connection.provider:
            raise ProviderSyncContextError(
                "The league and provider connection do not use the same provider."
            )

        if league.installation_id is None:
            raise ProviderSyncContextError("The league does not belong to an installation.")

        if provider_connection.installation_id != league.installation_id:
            raise ProviderSyncContextError(
                "The provider connection and league belong to different installations."
            )

        context = ProviderSyncContext(
            season=season,
            league=league,
            provider_connection=provider_connection,
        )

        _ = (
            context.provider_league_id,
            context.provider_season_id,
        )

        return context

    def update_season_configuration(
        self,
        *,
        context: ProviderSyncContext,
        provider_league: ProviderLeague,
    ) -> None:
        """Update canonical league and season configuration."""

        if provider_league.provider_league_id != context.provider_league_id:
            raise ProviderSyncContextError(
                "Provider league identity does not match the synchronization context."
            )

        if provider_league.provider_season_id != context.provider_season_id:
            raise ProviderSyncContextError(
                "Provider season identity does not match the synchronization context."
            )

        if provider_league.season_year != context.season.year:
            raise ProviderSyncContextError(
                "Provider season year does not match the synchronization context."
            )

        context.league.name = provider_league.name
        context.league.logo_url = provider_league.logo_url

        context.season.current_week = provider_league.current_week
        context.season.regular_season_weeks = provider_league.regular_season_weeks
        context.season.playoff_week_start = provider_league.playoff_week_start
        context.season.playoff_week_end = provider_league.playoff_week_end
        context.season.team_count = len(provider_league.teams)
        context.season.scoring_type = provider_league.scoring_type

        capabilities = provider_league.capabilities

        context.season.supports_head_to_head = capabilities.supports_head_to_head
        context.season.supports_weekly_matchups = capabilities.supports_weekly_matchups
        context.season.supports_lineup_history = capabilities.supports_lineup_history
        context.season.supports_optimal_lineups = capabilities.supports_optimal_lineups
        context.season.supports_schedule_swap = capabilities.supports_schedule_swap
        context.season.supports_all_play = capabilities.supports_all_play
        context.season.supports_league_average = capabilities.supports_league_average
        context.season.supports_team_logos = capabilities.supports_team_logos

        self.upsert_schedule_rule(
            context=context,
            schedule_rules=(provider_league.schedule_rules),
        )

    def upsert_schedule_rule(
        self,
        *,
        context: ProviderSyncContext,
        schedule_rules: ProviderScheduleRules,
    ) -> ScheduleRule:
        """Upsert one season-owned canonical schedule rule."""

        statement = (
            insert(ScheduleRule)
            .values(
                season_id=context.season_id,
                regular_season_weeks=(schedule_rules.regular_season_weeks),
                playoff_week_start=(schedule_rules.playoff_week_start),
                playoff_week_end=(schedule_rules.playoff_week_end),
                has_divisions=(schedule_rules.has_divisions),
                has_median_game=(schedule_rules.has_median_game),
                has_doubleheaders=(schedule_rules.has_doubleheaders),
                allows_ties=(schedule_rules.allows_ties),
                metadata=dict(schedule_rules.metadata),
            )
            .on_conflict_do_update(
                constraint=("uq_schedule_rules_season"),
                set_={
                    "regular_season_weeks": (schedule_rules.regular_season_weeks),
                    "playoff_week_start": (schedule_rules.playoff_week_start),
                    "playoff_week_end": (schedule_rules.playoff_week_end),
                    "has_divisions": (schedule_rules.has_divisions),
                    "has_median_game": (schedule_rules.has_median_game),
                    "has_doubleheaders": (schedule_rules.has_doubleheaders),
                    "allows_ties": (schedule_rules.allows_ties),
                    "metadata": dict(schedule_rules.metadata),
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(ScheduleRule.id)
        )

        rule_id = self.session.execute(statement).scalar_one()

        return self.session.get_one(
            ScheduleRule,
            rule_id,
        )

    def upsert_team(
        self,
        *,
        context: ProviderSyncContext,
        provider_team: ProviderTeam,
    ) -> Team:
        """Upsert one canonical provider team idempotently."""

        legacy_espn_team_id = legacy_espn_integer_id(
            provider=context.provider,
            provider_id=provider_team.provider_team_id,
            field_name="provider_team_id",
        )

        values = {
            "season_id": context.season_id,
            "espn_team_id": legacy_espn_team_id,
            "provider_team_id": (provider_team.provider_team_id),
            "name": provider_team.name,
            "abbreviation": (provider_team.abbreviation),
            "owner_display_name": (provider_team.owner_display_name),
            "logo_url": provider_team.logo_url,
            "is_active": provider_team.is_active,
            "wins": provider_team.wins,
            "losses": provider_team.losses,
            "ties": provider_team.ties,
            "points_for": provider_team.points_for,
            "points_against": (provider_team.points_against),
        }

        statement = (
            insert(Team)
            .values(**values)
            .on_conflict_do_update(
                constraint=("uq_teams_season_provider_team"),
                set_={
                    "espn_team_id": (legacy_espn_team_id),
                    "name": provider_team.name,
                    "abbreviation": (provider_team.abbreviation),
                    "owner_display_name": (provider_team.owner_display_name),
                    "logo_url": (provider_team.logo_url),
                    "is_active": (provider_team.is_active),
                    "wins": provider_team.wins,
                    "losses": provider_team.losses,
                    "ties": provider_team.ties,
                    "points_for": (provider_team.points_for),
                    "points_against": (provider_team.points_against),
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(Team.id)
        )

        team_id = self.session.execute(statement).scalar_one()

        return self.session.get_one(
            Team,
            team_id,
        )

    def upsert_teams(
        self,
        *,
        context: ProviderSyncContext,
        provider_teams: tuple[ProviderTeam, ...],
    ) -> tuple[Team, ...]:
        """Upsert a canonical season team collection."""

        provider_team_ids = [team.provider_team_id for team in provider_teams]

        if len(provider_team_ids) != len(set(provider_team_ids)):
            raise ProviderSyncContextError(
                "Provider team collection contains duplicate provider IDs."
            )

        return tuple(
            self.upsert_team(
                context=context,
                provider_team=provider_team,
            )
            for provider_team in provider_teams
        )

    def replace_matchups(
        self,
        *,
        context: ProviderSyncContext,
        provider_matchups: tuple[
            ProviderMatchup,
            ...,
        ],
        requested_weeks: tuple[int, ...],
    ) -> tuple[Matchup, ...]:
        """Replace canonical matchups for requested weeks."""

        normalized_weeks = tuple(sorted(set(requested_weeks)))

        if any(not isinstance(week, int) or week < 1 for week in normalized_weeks):
            raise ValueError("requested_weeks must contain positive integers.")

        team_map = self.get_team_map(
            season_id=context.season_id,
        )
        requested_week_set = set(normalized_weeks)

        resolved_matchups: list[
            tuple[
                ProviderMatchup,
                Team,
                Team | None,
            ]
        ] = []

        # Resolve and validate every provider ID before deleting
        # any existing database records.
        for provider_matchup in provider_matchups:
            if provider_matchup.week not in requested_week_set:
                raise ProviderSyncContextError("Provider matchup returned an unrequested week.")

            home_team = team_map.get(provider_matchup.home_provider_team_id)

            if home_team is None:
                raise ProviderSyncContextError(
                    "Provider matchup references unknown "
                    "home team "
                    f"{provider_matchup.home_provider_team_id!r}."
                )

            away_team: Team | None = None

            if provider_matchup.away_provider_team_id is not None:
                away_team = team_map.get(provider_matchup.away_provider_team_id)

                if away_team is None:
                    raise ProviderSyncContextError(
                        "Provider matchup references unknown "
                        "away team "
                        f"{provider_matchup.away_provider_team_id!r}."
                    )

            resolved_matchups.append(
                (
                    provider_matchup,
                    home_team,
                    away_team,
                )
            )

        if normalized_weeks:
            self.session.execute(
                delete(Matchup).where(
                    Matchup.season_id == context.season_id,
                    Matchup.week.in_(normalized_weeks),
                )
            )

        stored_matchups: list[Matchup] = []

        for (
            provider_matchup,
            home_team,
            away_team,
        ) in resolved_matchups:
            matchup = Matchup(
                season_id=context.season_id,
                week=provider_matchup.week,
                home_team_id=home_team.id,
                away_team_id=(away_team.id if away_team is not None else None),
                home_score=(provider_matchup.home_score),
                away_score=(provider_matchup.away_score),
                is_complete=(provider_matchup.is_complete),
                is_bye=provider_matchup.is_bye,
            )

            self.session.add(matchup)
            stored_matchups.append(matchup)

        self.session.flush()

        return tuple(stored_matchups)

    def replace_player_week_scores(
        self,
        *,
        context: ProviderSyncContext,
        provider_rosters: tuple[
            ProviderWeeklyRoster,
            ...,
        ],
        requested_weeks: tuple[int, ...],
    ) -> tuple[PlayerWeekScore, ...]:
        """Replace canonical player rows for requested weeks."""

        normalized_weeks = tuple(sorted(set(requested_weeks)))

        if any(not isinstance(week, int) or week < 1 for week in normalized_weeks):
            raise ValueError("requested_weeks must contain positive integers.")

        requested_week_set = set(normalized_weeks)
        team_map = self.get_team_map(
            season_id=context.season_id,
        )

        resolved_rosters: list[
            tuple[
                ProviderWeeklyRoster,
                Team,
            ]
        ] = []
        seen_team_weeks: set[tuple[int, str]] = set()

        # Validate all roster and player references before deleting
        # any existing player-week rows.
        for provider_roster in provider_rosters:
            if provider_roster.week not in requested_week_set:
                raise ProviderSyncContextError("Provider roster returned an unrequested week.")

            database_team = team_map.get(provider_roster.provider_team_id)

            if database_team is None:
                raise ProviderSyncContextError(
                    f"Provider roster references unknown team {provider_roster.provider_team_id!r}."
                )

            team_week = (
                provider_roster.week,
                provider_roster.provider_team_id,
            )

            if team_week in seen_team_weeks:
                raise ProviderSyncContextError(
                    "Provider returned duplicate roster data for one team and week."
                )

            seen_team_weeks.add(team_week)

            for player in provider_roster.players:
                legacy_espn_player_integer_id(
                    provider=context.provider,
                    provider_id=player.provider_player_id,
                )

            resolved_rosters.append(
                (
                    provider_roster,
                    database_team,
                )
            )

        if normalized_weeks:
            self.session.execute(
                delete(PlayerWeekScore).where(
                    PlayerWeekScore.season_id == context.season_id,
                    PlayerWeekScore.week.in_(normalized_weeks),
                )
            )

        stored_scores: list[PlayerWeekScore] = []

        for (
            provider_roster,
            database_team,
        ) in resolved_rosters:
            for player in provider_roster.players:
                legacy_player_id = legacy_espn_player_integer_id(
                    provider=context.provider,
                    provider_id=(player.provider_player_id),
                )

                raw_metadata = dict(player.metadata)
                raw_metadata["canonical_position"] = player.canonical_position

                player_score = PlayerWeekScore(
                    season_id=context.season_id,
                    team_id=database_team.id,
                    week=provider_roster.week,
                    espn_player_id=legacy_player_id,
                    provider_player_id=(player.provider_player_id),
                    provider_position=(player.provider_position),
                    player_name=player.player_name,
                    pro_team=player.pro_team,
                    default_position=(player.provider_position),
                    actual_lineup_slot=(player.actual_lineup_slot),
                    eligible_lineup_slots=list(player.eligible_lineup_slots),
                    points=player.points,
                    projected_points=(player.projected_points),
                    was_on_roster=(player.is_on_roster),
                    is_starter=player.is_starter,
                    is_bench=player.is_bench,
                    is_injured_reserve=(player.is_injured_reserve),
                    raw_metadata=raw_metadata,
                )

                self.session.add(player_score)
                stored_scores.append(player_score)

        self.session.flush()

        return tuple(stored_scores)

    def get_team_map(
        self,
        *,
        season_id: int,
    ) -> dict[str, Team]:
        """Return season teams keyed by canonical provider team ID."""

        teams = self.session.scalars(
            select(Team)
            .where(Team.season_id == season_id)
            .order_by(
                Team.provider_team_id,
                Team.id,
            )
        ).all()

        team_map: dict[str, Team] = {}

        for team in teams:
            if team.provider_team_id is None:
                raise ProviderSyncContextError(
                    f"Team {team.id} does not have a canonical provider ID."
                )

            if team.provider_team_id in team_map:
                raise ProviderSyncContextError(
                    f"Multiple teams use provider team ID {team.provider_team_id!r}."
                )

            team_map[team.provider_team_id] = team

        return team_map

    def create_sync_run(
        self,
        *,
        context: ProviderSyncContext,
        is_full_sync: bool,
        requested_weeks: tuple[int, ...],
        provider_request_metadata: dict[
            str,
            Any,
        ]
        | None = None,
    ) -> SyncRun:
        """Create one provider-aware synchronization run."""

        requested_from_week = min(requested_weeks) if requested_weeks else None
        requested_through_week = max(requested_weeks) if requested_weeks else None

        sync_run = SyncRun(
            season_id=context.season_id,
            provider_connection_id=(context.provider_connection_id),
            provider=context.provider,
            provider_request_metadata=(
                dict(provider_request_metadata) if provider_request_metadata is not None else None
            ),
            analytics_rebuilt=False,
            status=SyncStatus.RUNNING,
            is_full_sync=is_full_sync,
            requested_from_week=requested_from_week,
            requested_through_week=requested_through_week,
        )

        self.session.add(sync_run)
        self.session.flush()

        return sync_run

    def complete_sync_run(
        self,
        sync_run: SyncRun,
        *,
        teams_upserted: int,
        matchups_upserted: int,
        player_week_scores_upserted: int,
        analytics_rebuilt: bool,
    ) -> None:
        """Mark one synchronization run as successful."""

        for field_name, value in {
            "teams_upserted": teams_upserted,
            "matchups_upserted": matchups_upserted,
            "player_week_scores_upserted": (player_week_scores_upserted),
        }.items():
            if value < 0:
                raise ValueError(f"{field_name} must not be negative.")

        completed_at = datetime.now(UTC)

        sync_run.status = SyncStatus.SUCCEEDED
        sync_run.completed_at = completed_at
        sync_run.teams_upserted = teams_upserted
        sync_run.matchups_upserted = matchups_upserted
        sync_run.player_week_scores_upserted = player_week_scores_upserted
        sync_run.analytics_rebuilt = analytics_rebuilt

        if sync_run.season is not None:
            sync_run.season.last_synced_at = completed_at

    def fail_sync_run(
        self,
        sync_run: SyncRun,
        *,
        safe_error_message: str,
    ) -> None:
        """Mark one synchronization run as failed safely."""

        normalized_message = safe_error_message.strip()

        if not normalized_message:
            normalized_message = "Provider synchronization failed."

        sync_run.status = SyncStatus.FAILED
        sync_run.completed_at = datetime.now(UTC)
        sync_run.safe_error_message = normalized_message[:1000]
        sync_run.analytics_rebuilt = False


def legacy_espn_player_integer_id(
    *,
    provider: FantasyProvider,
    provider_id: str,
) -> int | None:
    """Return an ESPN player ID or None for another provider.

    ESPN assigns negative integer identifiers to NFL defenses,
    including values such as -16001 for the Falcons D/ST.
    """

    if provider is not FantasyProvider.ESPN:
        return None

    try:
        return int(provider_id)
    except ValueError as exc:
        raise ProviderSyncContextError(
            "provider_player_id must be an integer during the ESPN compatibility period."
        ) from exc


def legacy_espn_integer_id(
    *,
    provider: FantasyProvider,
    provider_id: str,
    field_name: str,
) -> int | None:
    """Return an ESPN integer ID or None for another provider."""

    if provider is not FantasyProvider.ESPN:
        return None

    try:
        parsed = int(provider_id)
    except ValueError as exc:
        raise ProviderSyncContextError(
            f"{field_name} must be numeric during the ESPN compatibility period."
        ) from exc

    if parsed < 1:
        raise ProviderSyncContextError(
            f"{field_name} must be positive during the ESPN compatibility period."
        )

    return parsed
