"""Provider-independent synchronization orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.providers.base import ProviderAdapter
from app.repositories.provider_sync_repository import (
    ProviderSyncRepository,
)
from app.services.provider_sync_policy import (
    select_completed_weeks,
    select_matchup_weeks,
    validate_provider_league,
    validate_provider_matchups,
    validate_provider_rosters,
)
from app.services.sync_lock import (
    acquire_season_sync_lock,
)


@dataclass(frozen=True, slots=True)
class ProviderSyncResult:
    """Summary of one canonical provider synchronization."""

    season_id: int
    provider_league_id: str
    provider_season_id: str
    current_week: int
    full_sync: bool
    matchup_weeks: tuple[int, ...]
    player_weeks: tuple[int, ...]
    teams_upserted: int
    matchups_upserted: int
    fantasy_byes_upserted: int
    player_week_scores_upserted: int
    analytics_rebuilt: bool


def synchronize_provider(
    *,
    session: Session,
    season_id: int,
    provider: ProviderAdapter,
    full_sync: bool,
    explicit_from_week: int | None = None,
    explicit_through_week: int | None = None,
) -> ProviderSyncResult:
    """Synchronize one internal season through a provider adapter."""

    repository = ProviderSyncRepository(session)
    context = repository.load_context(
        season_id=season_id,
    )

    if provider.provider is not context.provider:
        raise ValueError("The provider adapter does not match the season provider connection.")

    acquire_season_sync_lock(
        session,
        season_id=season_id,
    )

    provider_league = provider.fetch_league(
        provider_league_id=(context.provider_league_id),
        provider_season_id=(context.provider_season_id),
    )

    validate_provider_league(
        provider_league,
        expected_provider_league_id=(context.provider_league_id),
        expected_provider_season_id=(context.provider_season_id),
    )

    matchup_weeks = select_matchup_weeks(
        regular_season_weeks=(provider_league.regular_season_weeks),
        explicit_from_week=explicit_from_week,
        explicit_through_week=(explicit_through_week),
    )

    player_weeks = select_completed_weeks(
        current_week=provider_league.current_week,
        regular_season_weeks=(provider_league.regular_season_weeks),
        full_sync=full_sync,
        explicit_from_week=explicit_from_week,
        explicit_through_week=(explicit_through_week),
    )

    provider_matchups = provider.fetch_matchups(
        provider_league_id=(context.provider_league_id),
        provider_season_id=(context.provider_season_id),
        weeks=matchup_weeks,
    )

    provider_rosters = (
        provider.fetch_weekly_rosters(
            provider_league_id=(context.provider_league_id),
            provider_season_id=(context.provider_season_id),
            weeks=player_weeks,
        )
        if player_weeks
        else ()
    )

    expected_team_ids = {team.provider_team_id for team in provider_league.teams}

    validate_provider_matchups(
        provider_matchups,
        expected_team_ids=expected_team_ids,
        requested_weeks=matchup_weeks,
        allows_doubleheaders=(provider_league.schedule_rules.has_doubleheaders),
    )

    validate_provider_rosters(
        provider_rosters,
        expected_team_ids=expected_team_ids,
        requested_weeks=player_weeks,
    )

    sync_run = repository.create_sync_run(
        context=context,
        is_full_sync=full_sync,
        requested_weeks=player_weeks,
        provider_request_metadata={
            "matchup_weeks": list(matchup_weeks),
            "player_weeks": list(player_weeks),
            "explicit_from_week": (explicit_from_week),
            "explicit_through_week": (explicit_through_week),
        },
    )

    try:
        repository.update_season_configuration(
            context=context,
            provider_league=provider_league,
        )

        stored_teams = repository.upsert_teams(
            context=context,
            provider_teams=(provider_league.teams),
        )

        # Team inserts and updates must become visible before
        # string provider IDs are resolved to internal IDs.
        session.flush()

        stored_matchups = repository.replace_matchups(
            context=context,
            provider_matchups=(provider_matchups),
            requested_weeks=matchup_weeks,
        )

        stored_player_scores = repository.replace_player_week_scores(
            context=context,
            provider_rosters=provider_rosters,
            requested_weeks=player_weeks,
        )

        repository.complete_sync_run(
            sync_run,
            teams_upserted=len(stored_teams),
            matchups_upserted=(len(stored_matchups)),
            player_week_scores_upserted=(len(stored_player_scores)),
            analytics_rebuilt=False,
        )

        session.commit()
    except Exception:
        session.rollback()
        raise

    return ProviderSyncResult(
        season_id=context.season_id,
        provider_league_id=(context.provider_league_id),
        provider_season_id=(context.provider_season_id),
        current_week=provider_league.current_week,
        full_sync=full_sync,
        matchup_weeks=matchup_weeks,
        player_weeks=player_weeks,
        teams_upserted=len(stored_teams),
        matchups_upserted=len(stored_matchups),
        fantasy_byes_upserted=sum(matchup.is_bye for matchup in provider_matchups),
        player_week_scores_upserted=(len(stored_player_scores)),
        analytics_rebuilt=False,
    )
