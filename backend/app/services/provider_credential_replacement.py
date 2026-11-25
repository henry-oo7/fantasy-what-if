"""Safely replace one workspace's stored provider credentials.

Transaction boundaries (no provider network call runs inside a database
transaction):

1. Preparation: a short read-only transaction checks the workspace
   lifecycle, the active league, season, connection and claim, and captures
   a detached plan with safe markers. It closes before validation.
2. Validation: the submitted credentials are validated with the provider
   with no Session, connection, row lock or advisory lock.
3. Persistence: a new transaction locks the workspace, connection and claim
   (the workspace-first order used by synchronization and deletion),
   rechecks every prepared value, then encrypts and writes the new
   envelope. Any change during validation leaves the old credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import League, Season
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import (
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.providers.base import ProviderAdapter
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
from app.providers.credential_vault import (
    CREDENTIAL_SCHEMA_VERSION,
    ProviderCredentialConfigurationError,
    ProviderCredentialVault,
)
from app.providers.domain import ProviderCredentials
from app.providers.errors import (
    ProviderError,
    ProviderNotSupportedError,
)
from app.providers.registry import ProviderRegistry
from app.repositories.provider_league_claim_repository import (
    normalize_provider_league_id,
)
from app.services.provider_adapter_resolver import (
    build_provider_registry,
)
from app.services.provider_sync import credential_marker
from app.services.workspace_lifecycle import is_active_workspace

CHANGED_DURING_VALIDATION_MESSAGE = (
    "The league connection changed while the credentials were validated. Try again."
)


class ProviderCredentialReplacementError(RuntimeError):
    """Base error for provider credential replacement."""


class ProviderCredentialReplacementUnavailableError(ProviderCredentialReplacementError):
    """The workspace does not support credential replacement."""


class ProviderCredentialReplacementValidationError(ProviderCredentialReplacementError):
    """Replacement credentials failed safe validation."""


@dataclass(frozen=True, slots=True)
class ProviderCredentialReplacementResult:
    """Safe metadata returned after credential replacement."""

    workspace_id: int
    connection_id: int
    provider: str
    provider_league_id: str
    provider_season_id: str
    credential_version: int
    credential_schema_version: int
    validated_at: datetime


@dataclass(frozen=True, slots=True)
class _ReplacementPlan:
    """Detached preparation snapshot. No ORM objects and no credentials."""

    workspace_id: int
    league_id: int
    season_id: int
    connection_id: int
    claim_id: int
    provider: FantasyProvider
    league_provider_league_id: str
    claim_provider_league_id: str
    claimed_identity: str
    provider_season_id: str
    credential_marker: str

    def __repr__(self) -> str:
        return f"_ReplacementPlan(workspace_id={self.workspace_id})"


@dataclass(frozen=True, slots=True)
class _ActiveTarget:
    installation: Installation
    league: League
    season: Season
    connection: ProviderConnection
    claim: ProviderLeagueClaim
    claimed_identity: str


def replace_provider_credentials(
    session_factory: sessionmaker[Session],
    *,
    workspace_id: int,
    submitted_credentials: ProviderCredentials,
    credential_keyring: ProviderCredentialKeyring,
    registry: ProviderRegistry | None = None,
    now: datetime | None = None,
) -> ProviderCredentialReplacementResult:
    """Validate and atomically replace one active provider envelope."""

    # Phase 1: short preparation transaction, closed before provider I/O.
    with session_factory() as session:
        target = _load_active_target(session, workspace_id=workspace_id, lock=False)
        plan = _ReplacementPlan(
            workspace_id=target.installation.id,
            league_id=target.league.id,
            season_id=target.season.id,
            connection_id=target.connection.id,
            claim_id=target.claim.id,
            provider=target.connection.provider,
            league_provider_league_id=target.league.provider_league_id or "",
            claim_provider_league_id=target.claim.provider_league_id,
            claimed_identity=target.claimed_identity,
            provider_season_id=target.season.provider_season_id or "",
            credential_marker=credential_marker(target.connection),
        )
        session.rollback()

    replacement_values = dict(submitted_credentials.values)
    replacement_values["league_id"] = plan.league_provider_league_id
    replacement_values["season"] = plan.provider_season_id
    replacement_credentials = ProviderCredentials(values=replacement_values)

    active_registry = registry if registry is not None else build_provider_registry()

    try:
        provider = active_registry.create(plan.provider, replacement_credentials)
    except ProviderNotSupportedError as exc:
        raise ProviderCredentialReplacementUnavailableError(
            "The configured fantasy provider is not implemented."
        ) from exc

    # Phase 2: provider validation with no database session.
    _validate_with_provider(provider, plan)

    if not credential_keyring.configured:
        raise ProviderCredentialConfigurationError(
            "Provider credential encryption is not configured."
        )

    # Phase 3: one atomic persistence transaction.
    with session_factory() as session:
        try:
            result = _persist_replacement(
                session,
                plan=plan,
                replacement_credentials=replacement_credentials,
                credential_keyring=credential_keyring,
                timestamp=now or datetime.now(UTC),
            )
            session.commit()
        except BaseException:
            session.rollback()
            raise

    return result


def _validate_with_provider(provider: ProviderAdapter, plan: _ReplacementPlan) -> None:
    try:
        validation = provider.validate_configuration()

        if not validation.is_valid:
            raise ProviderCredentialReplacementValidationError(validation.safe_message)

        provider_league = provider.fetch_league(
            provider_league_id=plan.claim_provider_league_id,
            provider_season_id=plan.provider_season_id,
        )
    except ProviderCredentialReplacementValidationError:
        raise
    except ProviderError as exc:
        raise ProviderCredentialReplacementValidationError(exc.safe_message) from exc

    returned_identity = normalize_provider_league_id(provider_league.provider_league_id)

    if returned_identity != plan.claimed_identity:
        raise ProviderCredentialReplacementValidationError(
            "The replacement credentials do not access the claimed league."
        )

    if provider_league.provider_season_id.strip() != plan.provider_season_id.strip():
        raise ProviderCredentialReplacementValidationError(
            "The replacement credentials do not access the active season."
        )


def _persist_replacement(
    session: Session,
    *,
    plan: _ReplacementPlan,
    replacement_credentials: ProviderCredentials,
    credential_keyring: ProviderCredentialKeyring,
    timestamp: datetime,
) -> ProviderCredentialReplacementResult:
    target = _load_active_target(session, workspace_id=plan.workspace_id, lock=True)
    connection = target.connection

    if (
        target.league.id != plan.league_id
        or target.season.id != plan.season_id
        or connection.id != plan.connection_id
        or connection.provider is not plan.provider
        or target.league.provider_league_id != plan.league_provider_league_id
        or target.claim.id != plan.claim_id
        or target.claim.provider_league_id != plan.claim_provider_league_id
        or target.claimed_identity != plan.claimed_identity
        or (target.season.provider_season_id or "") != plan.provider_season_id
        or credential_marker(connection) != plan.credential_marker
    ):
        raise ProviderCredentialReplacementUnavailableError(CHANGED_DURING_VALIDATION_MESSAGE)

    vault = ProviderCredentialVault(
        keys=credential_keyring.keys,
    )
    encrypted_credentials = vault.encrypt(
        provider=connection.provider,
        credentials=replacement_credentials,
        key_version=credential_keyring.current_version,
    )

    connection.encrypted_credentials = encrypted_credentials
    connection.credential_version = credential_keyring.current_version
    connection.credential_schema_version = CREDENTIAL_SCHEMA_VERSION
    connection.last_validated_at = timestamp
    connection.last_validation_error = None

    session.flush()

    return ProviderCredentialReplacementResult(
        workspace_id=target.installation.id,
        connection_id=connection.id,
        provider=connection.provider.value,
        provider_league_id=target.claim.provider_league_id,
        provider_season_id=plan.provider_season_id,
        credential_version=connection.credential_version,
        credential_schema_version=(connection.credential_schema_version),
        validated_at=timestamp,
    )


def _load_active_target(
    session: Session,
    *,
    workspace_id: int,
    lock: bool,
) -> _ActiveTarget:
    """Resolve the replacement target; with ``lock``, workspace first."""

    installation = session.get(
        Installation,
        workspace_id,
        with_for_update=lock,
        populate_existing=lock,
    )

    if installation is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The requested workspace does not exist."
        )

    if not is_active_workspace(installation):
        raise ProviderCredentialReplacementUnavailableError("The workspace is not active.")

    if installation.active_league_id is None or installation.active_season_id is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The workspace does not have an active provider season."
        )

    league = session.get(League, installation.active_league_id, populate_existing=lock)
    season = session.get(Season, installation.active_season_id, populate_existing=lock)

    if league is None or season is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The active provider context is unavailable."
        )

    if league.installation_id != installation.id or season.league_id != league.id:
        raise ProviderCredentialReplacementUnavailableError(
            "The active provider context belongs to another workspace."
        )

    if (
        league.provider is None
        or league.provider_league_id is None
        or season.provider_season_id is None
        or season.provider_connection_id is None
    ):
        raise ProviderCredentialReplacementUnavailableError(
            "The active provider context is incomplete."
        )

    connection_statement = select(ProviderConnection).where(
        ProviderConnection.id == season.provider_connection_id,
        ProviderConnection.installation_id == installation.id,
        ProviderConnection.provider == league.provider,
        ProviderConnection.is_active.is_(True),
    )
    claim_statement = select(ProviderLeagueClaim).where(
        ProviderLeagueClaim.workspace_id == installation.id,
        ProviderLeagueClaim.league_id == league.id,
        ProviderLeagueClaim.provider == league.provider,
    )

    if lock:
        connection_statement = connection_statement.with_for_update().execution_options(
            populate_existing=True
        )
        claim_statement = claim_statement.with_for_update().execution_options(
            populate_existing=True
        )

    connection = session.scalar(connection_statement)

    if connection is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The active provider connection is unavailable."
        )

    claim = session.scalar(claim_statement)

    if claim is None:
        raise ProviderCredentialReplacementUnavailableError(
            "The workspace provider claim is unavailable."
        )

    claimed_identity = normalize_provider_league_id(claim.provider_league_id)
    active_identity = normalize_provider_league_id(league.provider_league_id)

    if (
        claim.normalized_provider_league_id != claimed_identity
        or claimed_identity != active_identity
    ):
        raise ProviderCredentialReplacementUnavailableError(
            "The workspace provider identity is inconsistent."
        )

    return _ActiveTarget(
        installation=installation,
        league=league,
        season=season,
        connection=connection,
        claim=claim,
        claimed_identity=claimed_identity,
    )
