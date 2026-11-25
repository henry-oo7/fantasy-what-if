"""Provider-independent synchronization orchestration.

Transaction boundaries (no provider network call runs inside a database
transaction):

1. Preparation: one short transaction resolves the target, checks the
   workspace lifecycle, records a ``running`` SyncRun, and captures a
   detached :class:`ProviderSyncPlan`. The caller commits and closes the
   Session before any provider call.
2. Fetch: every provider request runs with no Session, no checked-out
   connection, no row lock and no advisory lock. The result is plain
   provider-domain data.
3. Persistence: a new short transaction locks the workspace row, rechecks
   lifecycle, job authority, connection and provider target, acquires the
   season advisory lock, rejects stale fetches, then writes canonical and
   derived data and completes the SyncRun before commit.

The SyncRun id allocated during preparation is the freshness marker: a
fetch is stale when a SyncRun with a larger id for the same season has
already succeeded, because that attempt was prepared (and began fetching)
later.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.analytics.optimal_lineup import (
    calculate_team_week_optimal_lineup,
)
from app.db.models import SyncRun, SyncStatus
from app.db.platform_enums import (
    BackgroundJobStatus,
    FantasyProvider,
    OnboardingStage,
    SeasonStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation, ProviderConnection
from app.providers.base import ProviderAdapter
from app.providers.domain import (
    ProviderLeague,
    ProviderMatchup,
    ProviderWeeklyRoster,
)
from app.providers.errors import ProviderError
from app.repositories.optimal_lineup_repository import (
    replace_optimal_lineup_results,
)
from app.repositories.player_week_repository import (
    load_team_week_rosters,
)
from app.repositories.provider_sync_repository import (
    ProviderSyncContext,
    ProviderSyncRepository,
)
from app.services.background_job_worker import BackgroundJobExecutionError
from app.services.provider_sync_policy import (
    ProviderSyncValidationError,
    select_completed_weeks,
    select_matchup_weeks,
    validate_provider_league,
    validate_provider_matchups,
    validate_provider_rosters,
)
from app.services.sync_lock import (
    SyncAlreadyRunningError,
    acquire_season_sync_lock,
)
from app.services.workspace_lifecycle import is_active_workspace, is_deletion_pending

LOGGER = logging.getLogger(__name__)

WORKSPACE_DELETION_PENDING_SYNC_CODE = "workspace_unavailable"
SUPERSEDED_SYNC_MESSAGE = "A newer synchronization completed first; this result was discarded."
GENERIC_SYNC_FAILURE_MESSAGE = "Provider synchronization failed."
INVALID_PROVIDER_DATA_MESSAGE = "The provider returned invalid synchronization data."


@dataclass(frozen=True, slots=True)
class ProviderSyncResult:
    """Summary of one canonical provider synchronization."""

    provider: FantasyProvider
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
    # True when a newer synchronization committed first and this fetch was
    # discarded without writing canonical data.
    superseded: bool = False


class SyncWorkspaceRequirement(StrEnum):
    """Workspace lifecycle a synchronization must still satisfy at commit."""

    ACTIVE_SEASON = "active_season"
    INITIAL_ONBOARDING = "initial_onboarding"


@dataclass(frozen=True, slots=True)
class SyncJobAuthority:
    """The claimed background-job attempt that owns a synchronization."""

    job_id: int
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ProviderFetchTarget:
    """Detached values the provider adapter needs. No ORM, no secrets."""

    provider_league_id: str
    provider_season_id: str
    full_sync: bool
    explicit_from_week: int | None = None
    explicit_through_week: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderSyncPlan:
    """Immutable preparation snapshot carried across the provider fetch.

    Holds identifiers and safe markers only. Credential material stays in
    the adapter instance; the credential marker is a digest of ciphertext
    and key versions, never of plaintext.
    """

    workspace_id: int
    league_id: int
    season_id: int
    season_year: int
    provider: FantasyProvider
    provider_connection_id: int
    credential_marker: str
    sync_run_id: int
    requirement: SyncWorkspaceRequirement
    target: ProviderFetchTarget
    job_authority: SyncJobAuthority | None = None

    def __repr__(self) -> str:
        return (
            f"ProviderSyncPlan(workspace_id={self.workspace_id}, season_id={self.season_id}, "
            f"sync_run_id={self.sync_run_id}, requirement={self.requirement.value})"
        )


@dataclass(frozen=True, slots=True)
class ProviderSyncFetch:
    """Validated, detached provider-domain data from one fetch."""

    provider_league: ProviderLeague
    provider_matchups: tuple[ProviderMatchup, ...]
    provider_rosters: tuple[ProviderWeeklyRoster, ...]
    matchup_weeks: tuple[int, ...]
    player_weeks: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _WriteCounts:
    teams_upserted: int
    matchups_upserted: int
    player_week_scores_upserted: int
    analytics_rebuilt: bool


def credential_marker(connection: ProviderConnection) -> str:
    """Safe change marker for a connection's stored credential envelope."""

    material = "|".join(
        (
            str(connection.id),
            str(connection.credential_version),
            str(connection.credential_schema_version),
            connection.encrypted_credentials or "",
        )
    )

    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Public orchestration
# --------------------------------------------------------------------------


def synchronize_provider(
    *,
    session_factory: sessionmaker[Session],
    season_id: int,
    provider: ProviderAdapter,
    full_sync: bool,
    requirement: SyncWorkspaceRequirement = SyncWorkspaceRequirement.ACTIVE_SEASON,
    explicit_from_week: int | None = None,
    explicit_through_week: int | None = None,
    job_authority: SyncJobAuthority | None = None,
    expected_credential_marker: str | None = None,
) -> ProviderSyncResult:
    """Prepare, fetch and persist one season synchronization."""

    with session_factory() as session:
        plan = prepare_provider_sync(
            session,
            season_id=season_id,
            provider=provider,
            full_sync=full_sync,
            requirement=requirement,
            explicit_from_week=explicit_from_week,
            explicit_through_week=explicit_through_week,
            job_authority=job_authority,
            expected_credential_marker=expected_credential_marker,
        )
        session.commit()

    return execute_provider_sync(
        session_factory=session_factory,
        plan=plan,
        provider=provider,
    )


def prepare_provider_sync(
    session: Session,
    *,
    season_id: int,
    provider: ProviderAdapter,
    full_sync: bool,
    requirement: SyncWorkspaceRequirement = SyncWorkspaceRequirement.ACTIVE_SEASON,
    explicit_from_week: int | None = None,
    explicit_through_week: int | None = None,
    job_authority: SyncJobAuthority | None = None,
    expected_credential_marker: str | None = None,
) -> ProviderSyncPlan:
    """Phase 1. Validate the target and record a running SyncRun.

    Runs in the caller's transaction; the caller must commit and close the
    Session before calling :func:`execute_provider_sync`.
    """

    repository = ProviderSyncRepository(session)
    context = repository.load_context(season_id=season_id)

    if provider.provider is not context.provider:
        raise ValueError("The provider adapter does not match the season provider connection.")

    workspace_id = context.league.installation_id

    if workspace_id is None:  # load_context already rejects this.
        raise ValueError("The league does not belong to an installation.")

    _require_workspace_eligible(
        session.get(Installation, workspace_id),
        requirement=requirement,
        season_id=context.season_id,
        season_status=context.season.status,
    )

    marker = credential_marker(context.provider_connection)

    if expected_credential_marker is not None and marker != expected_credential_marker:
        raise _connection_changed()

    sync_run = repository.create_sync_run(
        context=context,
        is_full_sync=full_sync,
        requested_weeks=(),
        provider_request_metadata={
            "explicit_from_week": explicit_from_week,
            "explicit_through_week": explicit_through_week,
        },
    )

    return ProviderSyncPlan(
        workspace_id=workspace_id,
        league_id=context.league_id,
        season_id=context.season_id,
        season_year=context.season.year,
        provider=context.provider,
        provider_connection_id=context.provider_connection_id,
        credential_marker=marker,
        sync_run_id=sync_run.id,
        requirement=requirement,
        target=ProviderFetchTarget(
            provider_league_id=context.provider_league_id,
            provider_season_id=context.provider_season_id,
            full_sync=full_sync,
            explicit_from_week=explicit_from_week,
            explicit_through_week=explicit_through_week,
        ),
        job_authority=job_authority,
    )


def execute_provider_sync(
    *,
    session_factory: sessionmaker[Session],
    plan: ProviderSyncPlan,
    provider: ProviderAdapter,
) -> ProviderSyncResult:
    """Phases 2 and 3. Fetch with no transaction, then persist."""

    try:
        fetched = fetch_provider_sync_data(provider, plan.target)
    except BaseException as exc:
        record_sync_run_failure(
            session_factory,
            sync_run_id=plan.sync_run_id,
            safe_error_message=safe_sync_failure_message(exc),
        )
        raise

    return persist_provider_sync(
        session_factory=session_factory,
        plan=plan,
        fetched=fetched,
    )


# --------------------------------------------------------------------------
# Phase 2: provider fetch (no database access)
# --------------------------------------------------------------------------


def fetch_provider_sync_data(
    provider: ProviderAdapter,
    target: ProviderFetchTarget,
) -> ProviderSyncFetch:
    """Fetch and validate provider data. Never touches the database."""

    provider_league = provider.fetch_league(
        provider_league_id=target.provider_league_id,
        provider_season_id=target.provider_season_id,
    )

    validate_provider_league(
        provider_league,
        expected_provider_league_id=target.provider_league_id,
        expected_provider_season_id=target.provider_season_id,
    )

    matchup_weeks = select_matchup_weeks(
        regular_season_weeks=provider_league.regular_season_weeks,
        explicit_from_week=target.explicit_from_week,
        explicit_through_week=target.explicit_through_week,
    )

    player_weeks = select_completed_weeks(
        current_week=provider_league.current_week,
        regular_season_weeks=provider_league.regular_season_weeks,
        full_sync=target.full_sync,
        explicit_from_week=target.explicit_from_week,
        explicit_through_week=target.explicit_through_week,
    )

    provider_matchups = tuple(
        provider.fetch_matchups(
            provider_league_id=target.provider_league_id,
            provider_season_id=target.provider_season_id,
            weeks=matchup_weeks,
        )
    )

    provider_rosters = (
        tuple(
            provider.fetch_weekly_rosters(
                provider_league_id=target.provider_league_id,
                provider_season_id=target.provider_season_id,
                weeks=player_weeks,
            )
        )
        if player_weeks
        else ()
    )

    expected_team_ids = {team.provider_team_id for team in provider_league.teams}

    validate_provider_matchups(
        provider_matchups,
        expected_team_ids=expected_team_ids,
        requested_weeks=matchup_weeks,
        allows_doubleheaders=provider_league.schedule_rules.has_doubleheaders,
    )

    validate_provider_rosters(
        provider_rosters,
        expected_team_ids=expected_team_ids,
        requested_weeks=player_weeks,
    )

    return ProviderSyncFetch(
        provider_league=provider_league,
        provider_matchups=provider_matchups,
        provider_rosters=provider_rosters,
        matchup_weeks=matchup_weeks,
        player_weeks=player_weeks,
    )


# --------------------------------------------------------------------------
# Phase 3: persistence
# --------------------------------------------------------------------------


def persist_provider_sync(
    *,
    session_factory: sessionmaker[Session],
    plan: ProviderSyncPlan,
    fetched: ProviderSyncFetch,
) -> ProviderSyncResult:
    """Recheck the plan and persist fetched data in one short transaction."""

    try:
        with session_factory() as session:
            try:
                result = _persist_in_transaction(session, plan=plan, fetched=fetched)
                session.commit()
            except BaseException:
                session.rollback()
                raise
    except BaseException as exc:
        record_sync_run_failure(
            session_factory,
            sync_run_id=plan.sync_run_id,
            safe_error_message=safe_sync_failure_message(exc),
        )
        raise

    return result


def _persist_in_transaction(
    session: Session,
    *,
    plan: ProviderSyncPlan,
    fetched: ProviderSyncFetch,
) -> ProviderSyncResult:
    # The deletion request locks the same workspace row first, so either
    # this transaction commits before the request starts, or this lock
    # waits and then observes the committed deletion_pending state.
    workspace = session.scalar(
        select(Installation)
        .where(Installation.id == plan.workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if workspace is None or is_deletion_pending(workspace):
        raise _workspace_unavailable()

    repository = ProviderSyncRepository(session)
    context = repository.load_context(season_id=plan.season_id)

    _require_workspace_eligible(
        workspace,
        requirement=plan.requirement,
        season_id=plan.season_id,
        season_status=context.season.status,
    )
    _require_job_authority(session, plan.job_authority)

    try:
        acquire_season_sync_lock(session, season_id=plan.season_id)
    except SyncAlreadyRunningError as exc:
        raise BackgroundJobExecutionError(
            safe_error_code="sync_already_running",
            safe_error_message="Another synchronization is saving this season.",
            retryable=True,
        ) from exc

    _require_target_unchanged(context, plan)

    sync_run = session.scalar(
        select(SyncRun)
        .where(SyncRun.id == plan.sync_run_id, SyncRun.season_id == plan.season_id)
        .with_for_update()
    )

    if sync_run is None or sync_run.status is not SyncStatus.RUNNING:
        raise BackgroundJobExecutionError(
            safe_error_code="sync_run_unavailable",
            safe_error_message="The synchronization attempt is no longer current.",
        )

    if _newer_sync_succeeded(session, plan):
        repository.fail_sync_run(sync_run, safe_error_message=SUPERSEDED_SYNC_MESSAGE)

        return _result(plan, fetched, counts=None)

    counts = write_fetched_season_data(
        session,
        repository=repository,
        context=context,
        fetched=fetched,
        sync_run=sync_run,
        explicit_from_week=plan.target.explicit_from_week,
        explicit_through_week=plan.target.explicit_through_week,
    )

    return _result(plan, fetched, counts=counts)


def write_fetched_season_data(
    session: Session,
    *,
    repository: ProviderSyncRepository,
    context: ProviderSyncContext,
    fetched: ProviderSyncFetch,
    sync_run: SyncRun,
    explicit_from_week: int | None,
    explicit_through_week: int | None,
) -> _WriteCounts:
    """Write canonical and derived data inside the caller's transaction.

    The caller must hold the season advisory lock and owns commit/rollback.
    """

    player_weeks = fetched.player_weeks
    matchup_weeks = fetched.matchup_weeks

    repository.update_season_configuration(
        context=context,
        provider_league=fetched.provider_league,
    )

    stored_teams = repository.upsert_teams(
        context=context,
        provider_teams=fetched.provider_league.teams,
    )

    # Team inserts and updates must become visible before
    # string provider IDs are resolved to internal IDs.
    session.flush()

    stored_matchups = repository.replace_matchups(
        context=context,
        provider_matchups=fetched.provider_matchups,
        requested_weeks=matchup_weeks,
    )

    stored_player_scores = repository.replace_player_week_scores(
        context=context,
        provider_rosters=fetched.provider_rosters,
        requested_weeks=player_weeks,
    )

    analytics_rebuilt = bool(player_weeks)

    if analytics_rebuilt:
        affected_rosters = load_team_week_rosters(
            session,
            season_id=context.season_id,
            weeks=player_weeks,
        )

        calculated_results = tuple(
            calculate_team_week_optimal_lineup(roster) for roster in affected_rosters
        )

        replace_optimal_lineup_results(
            session,
            season_id=context.season_id,
            weeks=player_weeks,
            results=calculated_results,
        )

    sync_run.requested_from_week = min(player_weeks) if player_weeks else None
    sync_run.requested_through_week = max(player_weeks) if player_weeks else None
    sync_run.provider_request_metadata = {
        "matchup_weeks": list(matchup_weeks),
        "player_weeks": list(player_weeks),
        "explicit_from_week": explicit_from_week,
        "explicit_through_week": explicit_through_week,
    }

    counts = _WriteCounts(
        teams_upserted=len(stored_teams),
        matchups_upserted=len(stored_matchups),
        player_week_scores_upserted=len(stored_player_scores),
        analytics_rebuilt=analytics_rebuilt,
    )

    repository.complete_sync_run(
        sync_run,
        teams_upserted=counts.teams_upserted,
        matchups_upserted=counts.matchups_upserted,
        player_week_scores_upserted=counts.player_week_scores_upserted,
        analytics_rebuilt=counts.analytics_rebuilt,
    )
    session.flush()

    return counts


def build_sync_result(
    *,
    provider: FantasyProvider,
    season_id: int,
    target: ProviderFetchTarget,
    fetched: ProviderSyncFetch,
    counts: _WriteCounts | None,
) -> ProviderSyncResult:
    return ProviderSyncResult(
        provider=provider,
        season_id=season_id,
        provider_league_id=target.provider_league_id,
        provider_season_id=target.provider_season_id,
        current_week=fetched.provider_league.current_week,
        full_sync=target.full_sync,
        matchup_weeks=fetched.matchup_weeks,
        player_weeks=fetched.player_weeks,
        teams_upserted=counts.teams_upserted if counts else 0,
        matchups_upserted=counts.matchups_upserted if counts else 0,
        fantasy_byes_upserted=(
            sum(matchup.is_bye for matchup in fetched.provider_matchups) if counts else 0
        ),
        player_week_scores_upserted=counts.player_week_scores_upserted if counts else 0,
        analytics_rebuilt=counts.analytics_rebuilt if counts else False,
        superseded=counts is None,
    )


def _result(
    plan: ProviderSyncPlan,
    fetched: ProviderSyncFetch,
    *,
    counts: _WriteCounts | None,
) -> ProviderSyncResult:
    return build_sync_result(
        provider=plan.provider,
        season_id=plan.season_id,
        target=plan.target,
        fetched=fetched,
        counts=counts,
    )


# --------------------------------------------------------------------------
# Rechecks
# --------------------------------------------------------------------------


def _require_workspace_eligible(
    workspace: Installation | None,
    *,
    requirement: SyncWorkspaceRequirement,
    season_id: int,
    season_status: SeasonStatus,
) -> None:
    if workspace is None or is_deletion_pending(workspace):
        raise _workspace_unavailable()

    if requirement is SyncWorkspaceRequirement.INITIAL_ONBOARDING:
        if (
            workspace.registration_status is not WorkspaceRegistrationStatus.PROVISIONAL
            or workspace.onboarding_stage is not OnboardingStage.INITIAL_SYNC_RUNNING
        ):
            raise BackgroundJobExecutionError(
                safe_error_code="onboarding_state_invalid",
                safe_error_message="The workspace is not waiting for its first import.",
            )

        return

    if not is_active_workspace(workspace):
        raise _workspace_unavailable()

    if workspace.active_season_id != season_id or season_status is not SeasonStatus.ACTIVE:
        raise BackgroundJobExecutionError(
            safe_error_code="inactive_season_job",
            safe_error_message="The background job season is not the workspace's active season.",
        )


def _require_job_authority(
    session: Session,
    authority: SyncJobAuthority | None,
) -> None:
    if authority is None:
        return

    job = session.scalar(
        select(BackgroundJob)
        .where(BackgroundJob.id == authority.job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if (
        job is None
        or job.status is not BackgroundJobStatus.RUNNING
        or job.attempt_count != authority.attempt_count
        or job.lease_expires_at is None
        or job.lease_expires_at <= datetime.now(UTC)
    ):
        raise BackgroundJobExecutionError(
            safe_error_code="job_authority_lost",
            safe_error_message="The background job no longer owns this synchronization.",
        )


def _require_target_unchanged(
    context: ProviderSyncContext,
    plan: ProviderSyncPlan,
) -> None:
    # load_context already requires an active, same-workspace, same-provider
    # connection; a failure there raises ProviderSyncContextError.
    if (
        context.provider_connection_id != plan.provider_connection_id
        or credential_marker(context.provider_connection) != plan.credential_marker
    ):
        raise _connection_changed()

    if (
        context.league_id != plan.league_id
        or context.league.installation_id != plan.workspace_id
        or context.provider is not plan.provider
        or context.provider_league_id != plan.target.provider_league_id
        or context.provider_season_id != plan.target.provider_season_id
        or context.season.year != plan.season_year
    ):
        raise BackgroundJobExecutionError(
            safe_error_code="provider_target_changed",
            safe_error_message="The connected league changed during synchronization.",
            retryable=True,
        )


def _newer_sync_succeeded(session: Session, plan: ProviderSyncPlan) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    SyncRun.season_id == plan.season_id,
                    SyncRun.status == SyncStatus.SUCCEEDED,
                    SyncRun.id > plan.sync_run_id,
                )
            )
        )
    )


def _workspace_unavailable() -> BackgroundJobExecutionError:
    return BackgroundJobExecutionError(
        safe_error_code=WORKSPACE_DELETION_PENDING_SYNC_CODE,
        safe_error_message="The background job workspace is unavailable.",
        retryable=False,
    )


def _connection_changed() -> BackgroundJobExecutionError:
    return BackgroundJobExecutionError(
        safe_error_code="provider_connection_changed",
        safe_error_message="The saved league access changed during synchronization.",
        retryable=True,
    )


# --------------------------------------------------------------------------
# SyncRun failure bookkeeping
# --------------------------------------------------------------------------


def safe_sync_failure_message(error: BaseException) -> str:
    """Map a failure to a message safe for SyncRun history."""

    if isinstance(error, ProviderError):
        # Class-level constant only: instance messages may carry provider
        # detail, and SyncRun messages are returned by the status API.
        return type(error).default_safe_message

    if isinstance(error, BackgroundJobExecutionError):
        return error.safe_error_message

    if isinstance(error, ProviderSyncValidationError):
        return INVALID_PROVIDER_DATA_MESSAGE

    return GENERIC_SYNC_FAILURE_MESSAGE


def record_sync_run_failure(
    session_factory: sessionmaker[Session] | Callable[[], Session],
    *,
    sync_run_id: int,
    safe_error_message: str,
) -> None:
    """Mark a still-running SyncRun failed in its own short transaction.

    Never raises: the original synchronization failure stays authoritative.
    """

    values: dict[str, Any] = {
        "status": SyncStatus.FAILED,
        "completed_at": datetime.now(UTC),
        "safe_error_message": (safe_error_message.strip() or GENERIC_SYNC_FAILURE_MESSAGE)[:1000],
        "analytics_rebuilt": False,
    }

    try:
        with session_factory() as session:
            session.execute(
                update(SyncRun)
                .where(SyncRun.id == sync_run_id, SyncRun.status == SyncStatus.RUNNING)
                .values(**values)
            )
            session.commit()
    except Exception:  # noqa: BLE001 - bookkeeping must not mask the cause
        LOGGER.warning("Could not record synchronization failure: sync_run_id=%s.", sync_run_id)
