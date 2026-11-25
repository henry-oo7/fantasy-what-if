"""Import another season into one workspace's existing fantasy league.

Transaction boundaries (no provider network call runs inside a database
transaction):

1. Preparation: a short read-only transaction checks the workspace, league,
   duplicate year and single active connection, decrypts the stored
   credentials into the provider adapter (process memory only), and
   captures detached identifiers. It is closed before any provider call.
2. Fetch: provider validation, league snapshot, matchups and rosters run
   with no Session.
3. Persistence: one atomic transaction re-locks the workspace, league and
   connection, rechecks every prepared value, creates the Season, writes
   canonical and derived data under the season advisory lock, records the
   Season's first successful synchronization (``data_version`` 1, ``fresh``),
   and switches the active season. This is the same detached fetch/persist
   boundary the Season-owned synchronization uses; the API stays synchronous.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import model_registry  # noqa: F401
from app.db.models import League, Season
from app.db.platform_enums import FantasySport, SeasonStatus
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)
from app.providers.base import ProviderAdapter
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.providers.errors import ProviderNotSupportedError
from app.providers.registry import ProviderRegistry
from app.repositories.provider_sync_repository import ProviderSyncRepository
from app.services.provider_adapter_resolver import (
    build_provider_registry,
)
from app.services.provider_sync import (
    ProviderFetchTarget,
    ProviderSyncFetch,
    ProviderSyncResult,
    build_sync_result,
    credential_marker,
    fetch_provider_sync_data,
    write_fetched_season_data,
)
from app.services.season_lifecycle import (
    FutureSeasonImportError,
    ImportedSeasonLifecycle,
    classify_imported_season,
)
from app.services.season_sync_state import mark_succeeded, mark_syncing
from app.services.sync_lock import acquire_season_sync_lock
from app.services.workspace_lifecycle import is_active_workspace

SEASON_IMPORT_LOCK_KEY = 1_179_921_931


class SeasonImportError(RuntimeError):
    """Base error for season imports."""


class SeasonImportUnavailableError(SeasonImportError):
    """The installation cannot currently import a season."""


class SeasonImportConflictError(SeasonImportError):
    """The requested season is already imported."""


class SeasonImportValidationError(SeasonImportError):
    """The requested provider season failed validation."""


@dataclass(frozen=True, slots=True)
class SeasonImportResult:
    """Summary of one successful season import."""

    installation_id: int
    league_id: int
    season_id: int
    year: int
    status: SeasonStatus
    synchronization_enabled: bool
    sync_result: ProviderSyncResult


@dataclass(frozen=True, slots=True)
class _SeasonImportPlan:
    """Detached preparation snapshot. No ORM objects and no credentials."""

    workspace_id: int
    league_id: int
    provider_league_id: str
    league_sport: FantasySport | None
    provider_connection_id: int
    credential_marker: str
    season_year: int

    def __repr__(self) -> str:
        return (
            f"_SeasonImportPlan(workspace_id={self.workspace_id}, season_year={self.season_year})"
        )


def import_provider_season(
    session_factory: sessionmaker[Session],
    *,
    workspace_id: int,
    season_year: int,
    credential_keyring: ProviderCredentialKeyring,
    registry: ProviderRegistry | None = None,
    now: datetime | None = None,
) -> SeasonImportResult:
    """Import one season into the authenticated workspace's own league."""

    timestamp = now or datetime.now(UTC)

    try:
        lifecycle = classify_imported_season(
            season_year=season_year,
            now=timestamp,
        )
    except FutureSeasonImportError as exc:
        raise SeasonImportValidationError(str(exc)) from exc
    except ValueError as exc:
        raise SeasonImportValidationError(str(exc)) from exc

    # Phase 1: short preparation transaction, closed before provider I/O.
    with session_factory() as session:
        plan, provider = _prepare_import(
            session,
            workspace_id=workspace_id,
            season_year=season_year,
            credential_keyring=credential_keyring,
            registry=registry,
        )
        session.rollback()

    # Phase 2: provider requests with no database session.
    fetched = _fetch_import(provider, plan)

    # Phase 3: one atomic persistence transaction.
    with session_factory() as session:
        try:
            result = _persist_import(
                session,
                plan=plan,
                fetched=fetched,
                lifecycle=lifecycle,
                timestamp=timestamp,
            )
            session.commit()
        except BaseException:
            session.rollback()
            raise

    return result


def _prepare_import(
    session: Session,
    *,
    workspace_id: int,
    season_year: int,
    credential_keyring: ProviderCredentialKeyring,
    registry: ProviderRegistry | None,
) -> tuple[_SeasonImportPlan, ProviderAdapter]:
    installation = session.get(Installation, workspace_id)

    if installation is None or not is_active_workspace(installation):
        raise SeasonImportUnavailableError(
            "Another season can be imported only for an active workspace."
        )

    league = _single_league(session, workspace_id=installation.id, lock=False)

    if league.provider is None or league.provider_league_id is None:
        raise SeasonImportUnavailableError(
            "The installed league does not have canonical provider identity."
        )

    _reject_duplicate(session, league_id=league.id, season_year=season_year)

    connection = _single_connection(
        session,
        workspace_id=installation.id,
        league=league,
        lock=False,
    )

    if connection.encrypted_credentials is None or connection.credential_version is None:
        raise SeasonImportUnavailableError("The provider connection has no stored credentials.")

    vault = ProviderCredentialVault(
        keys=credential_keyring.keys,
    )

    envelope = vault.decrypt(
        encrypted_credentials=(connection.encrypted_credentials),
        key_version=connection.credential_version,
        expected_provider=connection.provider,
        expected_schema_version=(connection.credential_schema_version),
    )

    temporary_values = dict(envelope.credentials.values)
    temporary_values["league_id"] = league.provider_league_id
    temporary_values["season"] = str(season_year)

    temporary_credentials = ProviderCredentials(
        values=temporary_values,
    )

    active_registry = registry if registry is not None else build_provider_registry()

    try:
        provider = active_registry.create(
            connection.provider,
            temporary_credentials,
        )
    except ProviderNotSupportedError as exc:
        raise SeasonImportValidationError(
            "The configured fantasy provider is not implemented."
        ) from exc

    plan = _SeasonImportPlan(
        workspace_id=installation.id,
        league_id=league.id,
        provider_league_id=league.provider_league_id,
        league_sport=league.sport,
        provider_connection_id=connection.id,
        credential_marker=credential_marker(connection),
        season_year=season_year,
    )

    return plan, provider


def _fetch_import(
    provider: ProviderAdapter,
    plan: _SeasonImportPlan,
) -> ProviderSyncFetch:
    validation = provider.validate_configuration()

    if not validation.is_valid:
        raise SeasonImportValidationError(validation.safe_message)

    provider_league = provider.fetch_league(
        provider_league_id=plan.provider_league_id,
        provider_season_id=str(plan.season_year),
    )

    if provider_league.provider_league_id != plan.provider_league_id:
        raise SeasonImportValidationError("The provider returned a different league.")

    if (
        provider_league.provider_season_id != str(plan.season_year)
        or provider_league.season_year != plan.season_year
    ):
        raise SeasonImportValidationError("The provider returned a different season.")

    if plan.league_sport is not None and provider_league.sport is not plan.league_sport:
        raise SeasonImportValidationError("The provider season uses a different sport.")

    return fetch_provider_sync_data(provider, _import_target(plan))


def _import_target(plan: _SeasonImportPlan) -> ProviderFetchTarget:
    return ProviderFetchTarget(
        provider_league_id=plan.provider_league_id,
        provider_season_id=str(plan.season_year),
        full_sync=True,
    )


def _persist_import(
    session: Session,
    *,
    plan: _SeasonImportPlan,
    fetched: ProviderSyncFetch,
    lifecycle: ImportedSeasonLifecycle,
    timestamp: datetime,
) -> SeasonImportResult:
    # Imports serialize per workspace only; other workspaces are unaffected.
    session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                :lock_key,
                :workspace_id
            )
            """
        ),
        {
            "lock_key": SEASON_IMPORT_LOCK_KEY,
            "workspace_id": plan.workspace_id,
        },
    )

    installation = session.get(
        Installation,
        plan.workspace_id,
        with_for_update=True,
        populate_existing=True,
    )

    # A deletion request, suspension or other lifecycle change committed
    # during the provider fetch discards the fetched data.
    if installation is None or not is_active_workspace(installation):
        raise SeasonImportUnavailableError(
            "Another season can be imported only for an active workspace."
        )

    league = _single_league(session, workspace_id=installation.id, lock=True)

    if league.id != plan.league_id or league.provider_league_id != plan.provider_league_id:
        raise SeasonImportUnavailableError("The workspace league changed during the import.")

    _reject_duplicate(session, league_id=league.id, season_year=plan.season_year)

    connection = _single_connection(
        session,
        workspace_id=installation.id,
        league=league,
        lock=True,
    )

    if (
        connection.id != plan.provider_connection_id
        or credential_marker(connection) != plan.credential_marker
    ):
        raise SeasonImportUnavailableError(
            "The provider connection changed during the import. Try again."
        )

    provider_league = fetched.provider_league

    connection.last_validated_at = timestamp
    connection.last_validation_error = None

    season = Season(
        league_id=league.id,
        provider_connection_id=connection.id,
        provider_season_id=(provider_league.provider_season_id),
        year=provider_league.season_year,
        current_week=provider_league.current_week,
        regular_season_weeks=(provider_league.regular_season_weeks),
        status=SeasonStatus.DRAFT,
        team_count=len(provider_league.teams),
        playoff_week_start=(provider_league.playoff_week_start),
        playoff_week_end=(provider_league.playoff_week_end),
        scoring_type=provider_league.scoring_type,
    )

    session.add(season)
    session.flush()

    acquire_season_sync_lock(session, season_id=season.id)

    repository = ProviderSyncRepository(session)
    context = repository.load_context(season_id=season.id)
    # The Season row is new and locked by its own insert; this attempt is
    # its first authoritative synchronization.
    mark_syncing(season, attempt_at=timestamp)
    sync_run = repository.create_sync_run(
        context=context,
        is_full_sync=True,
        requested_weeks=(),
        provider_connection_id=connection.id,
    )
    counts = write_fetched_season_data(
        session,
        repository=repository,
        context=context,
        fetched=fetched,
        sync_run=sync_run,
        explicit_from_week=None,
        explicit_through_week=None,
    )
    assert sync_run.completed_at is not None
    mark_succeeded(season, completed_at=sync_run.completed_at)
    sync_result = build_sync_result(
        provider=context.provider,
        season_id=season.id,
        target=_import_target(plan),
        fetched=fetched,
        counts=counts,
    )

    if lifecycle.synchronization_enabled:
        previous_active_seasons = session.scalars(
            select(Season)
            .where(
                Season.league_id == league.id,
                Season.status == SeasonStatus.ACTIVE,
                Season.id != season.id,
            )
            .with_for_update()
        ).all()

        for previous_season in previous_active_seasons:
            previous_season.status = SeasonStatus.COMPLETED
            previous_season.completed_at = timestamp

    season.status = lifecycle.status
    season.activated_at = lifecycle.activated_at
    season.completed_at = lifecycle.completed_at

    installation.active_league_id = league.id

    if lifecycle.synchronization_enabled:
        installation.active_season_id = season.id

    session.flush()

    return SeasonImportResult(
        installation_id=installation.id,
        league_id=league.id,
        season_id=season.id,
        year=season.year,
        status=lifecycle.status,
        synchronization_enabled=(lifecycle.synchronization_enabled),
        sync_result=sync_result,
    )


def _single_league(session: Session, *, workspace_id: int, lock: bool) -> League:
    statement = select(League).where(League.installation_id == workspace_id).order_by(League.id)

    if lock:
        statement = statement.with_for_update()

    leagues = session.scalars(statement).all()

    if len(leagues) != 1:
        raise SeasonImportUnavailableError("The workspace must have exactly one fantasy league.")

    return leagues[0]


def _reject_duplicate(session: Session, *, league_id: int, season_year: int) -> None:
    duplicate_season_id = session.scalar(
        select(Season.id)
        .where(
            Season.league_id == league_id,
            Season.year == season_year,
        )
        .limit(1)
    )

    if duplicate_season_id is not None:
        raise SeasonImportConflictError(f"Season {season_year} is already imported.")


def _single_connection(
    session: Session,
    *,
    workspace_id: int,
    league: League,
    lock: bool,
) -> ProviderConnection:
    statement = (
        select(ProviderConnection)
        .where(
            ProviderConnection.installation_id == workspace_id,
            ProviderConnection.provider == league.provider,
            ProviderConnection.is_active.is_(True),
        )
        .order_by(ProviderConnection.id)
    )

    if lock:
        statement = statement.with_for_update()

    connections = session.scalars(statement).all()

    if len(connections) != 1:
        raise SeasonImportUnavailableError("Exactly one active provider connection is required.")

    return connections[0]
