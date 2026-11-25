"""League-Season-owned synchronization orchestration (ADR 0010).

Transaction boundaries (no provider network call runs inside a database
transaction):

1. Preparation: one short transaction locks the Season, rechecks job
   authority, selects the credential source, decrypts private credentials
   into the adapter (process memory only), marks the attempt ``syncing``,
   records a ``running`` SyncRun and captures ``data_version`` in a detached
   :class:`ProviderSyncPlan`. It commits and closes before any provider call.
2. Fetch: every provider request runs with no Session, no checked-out
   connection, no row lock and no advisory lock.
3. Persistence: a new short transaction re-locks the Season (and, for the
   legacy workspace fallback, the workspace row first), rechecks job
   authority, takes the Season advisory lock, rechecks the target and the
   credential source, rejects the write unless ``data_version`` is unchanged,
   then writes canonical and derived data, completes the SyncRun and advances
   the Season lifecycle before commit.

Credential source, in order:

- Public: some active access row was proven anonymously. Anonymous provider
  access; no connection is selected.
- Private: a user-owned connection whose owner holds active
  ``private_credentials`` access to this Season (most recently validated,
  then lowest ID). Which connection was used is never returned or recorded.
- Transitional workspace fallback: only when nobody holds active personal
  access and the Season's League still belongs to a workspace. Uses only that
  workspace's own connection under the workspace lifecycle rules. Retired by
  the identity cutover.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.analytics.optimal_lineup import (
    calculate_team_week_optimal_lineup,
)
from app.core.settings import Settings
from app.db.models import Season, SyncRun, SyncStatus
from app.db.platform_enums import (
    BackgroundJobStatus,
    FantasyProvider,
    OnboardingStage,
    ProviderAccessMode,
    SeasonStatus,
    SeasonSyncState,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation, ProviderConnection
from app.providers.base import ProviderAdapter
from app.providers.domain import (
    ProviderCredentials,
    ProviderLeague,
    ProviderMatchup,
    ProviderWeeklyRoster,
)
from app.providers.errors import ProviderError, ProviderNotSupportedError
from app.providers.registry import ProviderRegistry
from app.repositories.optimal_lineup_repository import (
    replace_optimal_lineup_results,
)
from app.repositories.player_week_repository import (
    load_team_week_rosters,
)
from app.repositories.provider_league_claim_repository import claimed_workspace_id
from app.repositories.provider_sync_repository import (
    ProviderSyncContext,
    ProviderSyncContextError,
    ProviderSyncContextNotFoundError,
    ProviderSyncRepository,
)
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    provider_error_code,
)
from app.services.provider_adapter_resolver import (
    ProviderAdapterResolutionError,
    build_provider_registry,
    resolve_connection_credentials,
)
from app.services.provider_sync_policy import (
    ProviderSyncValidationError,
    select_completed_weeks,
    select_matchup_weeks,
    validate_provider_league,
    validate_provider_matchups,
    validate_provider_rosters,
)
from app.services.season_lifecycle_reconciliation import reconcile_stale_active_season
from app.services.season_sync_state import (
    mark_failed,
    mark_succeeded,
    mark_syncing,
    safe_failure_code,
)
from app.services.sync_lock import (
    SyncAlreadyRunningError,
    acquire_season_sync_lock,
)
from app.services.workspace_lifecycle import is_active_workspace, is_deletion_pending

LOGGER = logging.getLogger(__name__)

WORKSPACE_DELETION_PENDING_SYNC_CODE = "workspace_unavailable"
REVERIFICATION_REQUIRED_CODE = "provider_reverification_required"
SUPERSEDED_SYNC_MESSAGE = "A newer synchronization completed first; this result was discarded."
GENERIC_SYNC_FAILURE_MESSAGE = "Provider synchronization failed."
INVALID_PROVIDER_DATA_MESSAGE = "The provider returned invalid synchronization data."

# Preparation failures that must not touch Season state: the job no longer
# owns the attempt, or the Season is gone.
_NO_SEASON_OUTCOME_CODES = frozenset({"job_authority_lost", "season_unavailable"})


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
    # True when another synchronization committed first and this fetch was
    # discarded without writing canonical data.
    superseded: bool = False


class SyncWorkspaceRequirement(StrEnum):
    """Workspace lifecycle the transitional workspace fallback must satisfy."""

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

    Identifiers and safe markers only. Credential material stays inside the
    adapter instance. ``provider_connection_id`` and ``credential_marker`` are
    internal recheck markers (a digest of ciphertext and key versions, never of
    plaintext) and never leave the worker; no owner identity is held.
    """

    season_id: int
    league_id: int
    season_year: int
    provider: FantasyProvider
    access_mode: ProviderAccessMode
    provider_connection_id: int | None
    credential_marker: str | None
    legacy_workspace_id: int | None
    requirement: SyncWorkspaceRequirement
    expected_data_version: int
    sync_run_id: int
    target: ProviderFetchTarget
    job_authority: SyncJobAuthority | None = None

    def __repr__(self) -> str:
        return (
            f"ProviderSyncPlan(season_id={self.season_id}, sync_run_id={self.sync_run_id}, "
            f"access_mode={self.access_mode.value}, "
            f"expected_data_version={self.expected_data_version})"
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


@dataclass(frozen=True, slots=True)
class _CredentialSource:
    access_mode: ProviderAccessMode
    connection: ProviderConnection | None
    legacy_workspace_id: int | None


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


def synchronize_season(
    *,
    session_factory: sessionmaker[Session],
    season_id: int,
    settings: Settings,
    registry: ProviderRegistry | None = None,
    job_authority: SyncJobAuthority | None = None,
    requirement: SyncWorkspaceRequirement = SyncWorkspaceRequirement.ACTIVE_SEASON,
    expected_credential_marker: str | None = None,
) -> ProviderSyncResult:
    """Prepare, fetch and persist one full League-Season synchronization."""

    plan, provider = prepare_season_sync(
        session_factory=session_factory,
        season_id=season_id,
        settings=settings,
        registry=registry,
        job_authority=job_authority,
        requirement=requirement,
        expected_credential_marker=expected_credential_marker,
    )

    return execute_provider_sync(
        session_factory=session_factory,
        plan=plan,
        provider=provider,
    )


def prepare_season_sync(
    *,
    session_factory: sessionmaker[Session],
    season_id: int,
    settings: Settings,
    registry: ProviderRegistry | None = None,
    job_authority: SyncJobAuthority | None = None,
    requirement: SyncWorkspaceRequirement = SyncWorkspaceRequirement.ACTIVE_SEASON,
    expected_credential_marker: str | None = None,
) -> tuple[ProviderSyncPlan, ProviderAdapter]:
    """Phase 1 in its own short transaction, committed and closed on return."""

    try:
        with session_factory() as session:
            plan, provider = _prepare_in_transaction(
                session,
                season_id=season_id,
                settings=settings,
                registry=registry,
                job_authority=job_authority,
                requirement=requirement,
                expected_credential_marker=expected_credential_marker,
            )
            session.commit()
    except BackgroundJobExecutionError as exc:
        if exc.safe_error_code not in _NO_SEASON_OUTCOME_CODES:
            _record_unstarted_attempt_failure(
                session_factory, season_id=season_id, failure_code=exc.safe_error_code
            )
        raise

    return plan, provider


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
        record_sync_failure(session_factory, plan=plan, error=exc)
        raise

    return persist_provider_sync(
        session_factory=session_factory,
        plan=plan,
        fetched=fetched,
    )


def _prepare_in_transaction(
    session: Session,
    *,
    season_id: int,
    settings: Settings,
    registry: ProviderRegistry | None,
    job_authority: SyncJobAuthority | None,
    requirement: SyncWorkspaceRequirement,
    expected_credential_marker: str | None,
) -> tuple[ProviderSyncPlan, ProviderAdapter]:
    repository = ProviderSyncRepository(session)
    workspace_id = claimed_workspace_id(session, _load_context(repository, season_id).league)

    # Transitional: a workspace's prior-year active Season is completed before
    # its fallback sync, exactly as the retired active-season path did.
    if (
        workspace_id is not None
        and requirement is SyncWorkspaceRequirement.ACTIVE_SEASON
        and not repository.has_active_access(season_id=season_id)
    ):
        reconcile_stale_active_season(session, workspace_id=workspace_id)

    context = _load_context(repository, season_id, lock=True)
    _require_job_authority(session, job_authority)

    source = _select_credential_source(session, repository, context, requirement=requirement)
    marker = credential_marker(source.connection) if source.connection is not None else None

    if expected_credential_marker is not None and marker != expected_credential_marker:
        raise _connection_changed()

    target = ProviderFetchTarget(
        provider_league_id=context.provider_league_id,
        provider_season_id=context.provider_season_id,
        full_sync=True,
    )
    provider = _build_adapter(
        source,
        context=context,
        target=target,
        settings=settings,
        registry=registry,
    )

    mark_syncing(context.season, attempt_at=datetime.now(UTC))
    sync_run = repository.create_sync_run(
        context=context,
        is_full_sync=True,
        requested_weeks=(),
        provider_connection_id=(
            source.connection.id
            if source.connection is not None and source.legacy_workspace_id is not None
            else None
        ),
    )

    plan = ProviderSyncPlan(
        season_id=context.season_id,
        league_id=context.league_id,
        season_year=context.season.year,
        provider=context.provider,
        access_mode=source.access_mode,
        provider_connection_id=source.connection.id if source.connection is not None else None,
        credential_marker=marker,
        legacy_workspace_id=source.legacy_workspace_id,
        requirement=requirement,
        expected_data_version=context.season.data_version,
        sync_run_id=sync_run.id,
        target=target,
        job_authority=job_authority,
    )

    return plan, provider


# --------------------------------------------------------------------------
# Credential source selection
# --------------------------------------------------------------------------


def _select_credential_source(
    session: Session,
    repository: ProviderSyncRepository,
    context: ProviderSyncContext,
    *,
    requirement: SyncWorkspaceRequirement,
) -> _CredentialSource:
    season_id = context.season_id

    if repository.has_active_public_access(season_id=season_id):
        return _CredentialSource(ProviderAccessMode.PUBLIC, None, None)

    connections = repository.eligible_private_connections(
        season_id=season_id, provider=context.provider
    )

    if connections:
        return _CredentialSource(ProviderAccessMode.PRIVATE, connections[0], None)

    workspace_id = claimed_workspace_id(session, context.league)

    if workspace_id is not None and not repository.has_active_access(season_id=season_id):
        return _legacy_workspace_source(
            context,
            workspace=session.get(Installation, workspace_id),
            requirement=requirement,
            session=session,
        )

    raise _reverification_required()


def _legacy_workspace_source(
    context: ProviderSyncContext,
    *,
    workspace: Installation | None,
    requirement: SyncWorkspaceRequirement,
    session: Session,
) -> _CredentialSource:
    """Shortcut: transitional workspace credentials, removed at the identity cutover."""

    _require_workspace_eligible(
        workspace,
        requirement=requirement,
        season_id=context.season_id,
        season_status=context.season.status,
    )
    assert workspace is not None

    connection_id = context.season.provider_connection_id
    connection = (
        session.scalar(
            select(ProviderConnection)
            .where(ProviderConnection.id == connection_id)
            .execution_options(populate_existing=True)
        )
        if connection_id is not None
        else None
    )

    if (
        connection is None
        or connection.installation_id != workspace.id
        or connection.user_id is not None
        or not connection.is_active
        or connection.provider is not context.provider
    ):
        raise _reverification_required()

    if connection.access_mode is ProviderAccessMode.PUBLIC:
        return _CredentialSource(ProviderAccessMode.PUBLIC, connection, workspace.id)

    if connection.encrypted_credentials is None or connection.credential_version is None:
        raise _reverification_required()

    return _CredentialSource(ProviderAccessMode.PRIVATE, connection, workspace.id)


def _build_adapter(
    source: _CredentialSource,
    *,
    context: ProviderSyncContext,
    target: ProviderFetchTarget,
    settings: Settings,
    registry: ProviderRegistry | None,
) -> ProviderAdapter:
    values: dict[str, str] = {}

    if source.access_mode is ProviderAccessMode.PRIVATE:
        assert source.connection is not None

        try:
            stored, _ = resolve_connection_credentials(
                connection=source.connection,
                season=context.season,
                settings=settings,
            )
        except ProviderAdapterResolutionError as exc:
            raise BackgroundJobExecutionError(
                safe_error_code="provider_credentials_unavailable",
                safe_error_message="The saved league access could not be used.",
            ) from exc

        values.update(stored.values)

    # The canonical Season is the target, whatever target the connection
    # was first validated against.
    values["league_id"] = target.provider_league_id
    values["season"] = target.provider_season_id

    active_registry = registry if registry is not None else build_provider_registry()

    try:
        provider = active_registry.create(context.provider, ProviderCredentials(values=values))
    except ProviderNotSupportedError as exc:
        raise BackgroundJobExecutionError(
            safe_error_code="provider_not_supported",
            safe_error_message="The fantasy provider is not implemented.",
        ) from exc

    # ProviderRegistry.create already refuses an adapter for another provider.
    return provider


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
        record_sync_failure(session_factory, plan=plan, error=exc)
        raise

    return result


def _persist_in_transaction(
    session: Session,
    *,
    plan: ProviderSyncPlan,
    fetched: ProviderSyncFetch,
) -> ProviderSyncResult:
    workspace: Installation | None = None

    if plan.legacy_workspace_id is not None:
        # Workspace before Season, the order every workspace writer uses. The
        # deletion request locks this row first, so either this transaction
        # commits before the request starts, or it waits and then observes
        # the committed deletion_pending state.
        workspace = session.scalar(
            select(Installation)
            .where(Installation.id == plan.legacy_workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        if workspace is None or is_deletion_pending(workspace):
            raise _workspace_unavailable()

    repository = ProviderSyncRepository(session)
    context = _load_context(repository, plan.season_id, lock=True)
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
    _require_credential_source_unchanged(session, repository, context, plan, workspace)

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

    # data_version is the durable stale-write marker: another attempt has
    # committed since this one started, so its result wins and ours is dropped.
    if context.season.data_version != plan.expected_data_version:
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
    assert sync_run.completed_at is not None
    mark_succeeded(context.season, completed_at=sync_run.completed_at)
    session.flush()

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

    The caller must hold the season advisory lock, owns commit/rollback, and
    advances the Season lifecycle after this returns.
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


def _load_context(
    repository: ProviderSyncRepository,
    season_id: int,
    *,
    lock: bool = False,
) -> ProviderSyncContext:
    try:
        return repository.load_context(season_id=season_id, lock=lock)
    except ProviderSyncContextNotFoundError as exc:
        raise BackgroundJobExecutionError(
            safe_error_code="season_unavailable",
            safe_error_message="The background job season is unavailable.",
        ) from exc
    except ProviderSyncContextError as exc:
        raise BackgroundJobExecutionError(
            safe_error_code="season_target_invalid",
            safe_error_message="The season does not have a valid provider target.",
        ) from exc


def _require_workspace_eligible(
    workspace: Installation | None,
    *,
    requirement: SyncWorkspaceRequirement,
    season_id: int,
    season_status: SeasonStatus | None,
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
    if (
        context.league_id != plan.league_id
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


def _require_credential_source_unchanged(
    session: Session,
    repository: ProviderSyncRepository,
    context: ProviderSyncContext,
    plan: ProviderSyncPlan,
    workspace: Installation | None,
) -> None:
    """The fetch is persisted only while its credential source still authorizes it."""

    season_id = plan.season_id

    if plan.legacy_workspace_id is not None:
        if repository.has_active_access(season_id=season_id):
            raise _access_mode_changed()

        if claimed_workspace_id(session, context.league) != plan.legacy_workspace_id:
            raise _connection_changed()

        source = _legacy_workspace_source(
            context, workspace=workspace, requirement=plan.requirement, session=session
        )
    else:
        public = repository.has_active_public_access(season_id=season_id)

        if public is not (plan.access_mode is ProviderAccessMode.PUBLIC):
            raise _access_mode_changed()

        if public:
            return

        connections = repository.eligible_private_connections(
            season_id=season_id,
            provider=context.provider,
            connection_id=plan.provider_connection_id,
        )

        if not connections:
            raise _connection_changed()

        source = _CredentialSource(ProviderAccessMode.PRIVATE, connections[0], None)

    if source.access_mode is not plan.access_mode:
        raise _access_mode_changed()

    if (
        source.connection is None
        or source.connection.id != plan.provider_connection_id
        or credential_marker(source.connection) != plan.credential_marker
    ):
        raise _connection_changed()


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


def _access_mode_changed() -> BackgroundJobExecutionError:
    return BackgroundJobExecutionError(
        safe_error_code="provider_access_mode_changed",
        safe_error_message="The league's access changed during synchronization.",
        retryable=True,
    )


def _reverification_required() -> BackgroundJobExecutionError:
    return BackgroundJobExecutionError(
        safe_error_code=REVERIFICATION_REQUIRED_CODE,
        safe_error_message="No saved league access can currently synchronize this season.",
        retryable=False,
    )


# --------------------------------------------------------------------------
# Failure bookkeeping
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


def sync_failure_code(error: BaseException) -> str:
    """Bounded safe Season failure code; never provider text."""

    if isinstance(error, BackgroundJobExecutionError):
        return safe_failure_code(error.safe_error_code)

    if isinstance(error, ProviderError):
        return safe_failure_code(provider_error_code(error))

    if isinstance(error, ProviderSyncValidationError):
        return "invalid_provider_data"

    return safe_failure_code(None)


def record_sync_failure(
    session_factory: sessionmaker[Session],
    *,
    plan: ProviderSyncPlan,
    error: BaseException,
) -> None:
    """Short outcome transaction for a started attempt that did not persist.

    Fails the attempt's SyncRun, keeps every canonical row and ``data_version``,
    and moves the Season to ``stale`` or ``failed`` only while this attempt is
    still the newest one: ``syncing``, the same ``data_version``, and no newer
    SyncRun. Never raises; the original failure stays authoritative.
    """

    message = (safe_sync_failure_message(error).strip() or GENERIC_SYNC_FAILURE_MESSAGE)[:1000]

    try:
        with session_factory() as session:
            season = session.scalar(
                select(Season)
                .where(Season.id == plan.season_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            session.execute(
                update(SyncRun)
                .where(SyncRun.id == plan.sync_run_id, SyncRun.status == SyncStatus.RUNNING)
                .values(
                    status=SyncStatus.FAILED,
                    completed_at=datetime.now(UTC),
                    safe_error_message=message,
                    analytics_rebuilt=False,
                )
            )
            newer_attempt = session.scalar(
                select(
                    exists().where(
                        SyncRun.season_id == plan.season_id,
                        SyncRun.id > plan.sync_run_id,
                    )
                )
            )

            if (
                season is not None
                and season.sync_state is SeasonSyncState.SYNCING
                and season.data_version == plan.expected_data_version
                and not newer_attempt
            ):
                mark_failed(season, failure_code=sync_failure_code(error))

            session.commit()
    except Exception:  # noqa: BLE001 - bookkeeping must not mask the cause
        LOGGER.warning(
            "Could not record synchronization failure: season_id=%s, sync_run_id=%s.",
            plan.season_id,
            plan.sync_run_id,
        )


def _record_unstarted_attempt_failure(
    session_factory: sessionmaker[Session],
    *,
    season_id: int,
    failure_code: str,
) -> None:
    """Record why an attempt could not start, unless another attempt is syncing."""

    try:
        with session_factory() as session:
            season = session.scalar(select(Season).where(Season.id == season_id).with_for_update())

            if season is not None and season.sync_state is not SeasonSyncState.SYNCING:
                mark_failed(season, failure_code=failure_code)

            session.commit()
    except Exception:  # noqa: BLE001 - bookkeeping must not mask the cause
        LOGGER.warning("Could not record synchronization start failure: season_id=%s.", season_id)
