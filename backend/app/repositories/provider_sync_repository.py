"""Persistence boundary for provider-independent synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    Season,
    SyncRun,
    SyncStatus,
    Team,
)
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import (
    ProviderConnection,
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
