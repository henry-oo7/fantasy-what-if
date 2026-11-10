"""Atomically finalize installation setup."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import League, Season
from app.db.platform_enums import (
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderConnection,
)
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.errors import ProviderNotSupportedError
from app.providers.registry import ProviderRegistry
from app.services.provider_adapter_resolver import (
    build_provider_registry,
)
from app.services.provider_sync import (
    ProviderSyncResult,
    synchronize_provider,
)
from app.services.season_lifecycle import (
    FutureSeasonImportError,
    classify_imported_season,
)

SETUP_FINALIZATION_LOCK_KEY = 1_179_921_930


class SetupFinalizationError(RuntimeError):
    """Base error for installation finalization."""


class SetupFinalizationUnavailableError(SetupFinalizationError):
    """The installation cannot currently be finalized."""


class SetupFinalizationValidationError(SetupFinalizationError):
    """Stored provider configuration could not be validated."""


@dataclass(frozen=True, slots=True)
class SetupFinalizationResult:
    """Summary of one successful setup finalization."""

    installation_id: int
    league_id: int
    season_id: int
    sync_result: ProviderSyncResult


def finalize_installation_setup(
    session: Session,
    *,
    credential_key: str,
    registry: ProviderRegistry | None = None,
    now: datetime | None = None,
) -> SetupFinalizationResult:
    """Create, synchronize, and activate the configured season."""

    session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                :lock_key
            )
            """
        ),
        {
            "lock_key": SETUP_FINALIZATION_LOCK_KEY,
        },
    )

    installations = session.scalars(
        select(Installation).order_by(Installation.id).with_for_update()
    ).all()

    if len(installations) != 1:
        raise SetupFinalizationUnavailableError(
            "Exactly one installation must exist before setup can be finalized."
        )

    installation = installations[0]

    if installation.status is not InstallationStatus.SETUP_IN_PROGRESS:
        raise SetupFinalizationUnavailableError(
            "Setup can be finalized only while setup is in progress."
        )

    if installation.active_league_id is not None or installation.active_season_id is not None:
        raise SetupFinalizationUnavailableError(
            "The installation already has active league or season references."
        )

    connections = session.scalars(
        select(ProviderConnection)
        .where(
            ProviderConnection.installation_id == installation.id,
            ProviderConnection.is_active.is_(True),
        )
        .order_by(ProviderConnection.id)
        .with_for_update()
    ).all()

    if len(connections) != 1:
        raise SetupFinalizationUnavailableError(
            "Exactly one active provider connection is required."
        )

    connection = connections[0]

    if connection.encrypted_credentials is None:
        raise SetupFinalizationUnavailableError(
            "The provider connection has no stored credentials."
        )

    existing_league_id = session.scalar(
        select(League.id).where(League.installation_id == installation.id).limit(1)
    )

    if existing_league_id is not None:
        raise SetupFinalizationUnavailableError(
            "A league is already configured for this installation."
        )

    vault = ProviderCredentialVault(
        keys={
            connection.credential_version: credential_key,
        }
    )

    envelope = vault.decrypt(
        encrypted_credentials=(connection.encrypted_credentials),
        key_version=connection.credential_version,
        expected_provider=connection.provider,
    )

    active_registry = registry if registry is not None else build_provider_registry()

    try:
        provider = active_registry.create(
            connection.provider,
            envelope.credentials,
        )
    except ProviderNotSupportedError as exc:
        raise SetupFinalizationValidationError(
            "The configured fantasy provider is not implemented."
        ) from exc

    validation = provider.validate_configuration()

    if not validation.is_valid:
        connection.last_validation_error = validation.safe_message

        raise SetupFinalizationValidationError(validation.safe_message)

    connection.last_validated_at = now or datetime.now(UTC)
    connection.last_validation_error = None

    references = provider.discover_leagues()

    if len(references) != 1:
        raise SetupFinalizationValidationError(
            "Setup requires exactly one configured provider league."
        )

    reference = references[0]

    provider_league = provider.fetch_league(
        provider_league_id=(reference.provider_league_id),
        provider_season_id=(reference.provider_season_id),
    )

    if (
        provider_league.provider_league_id != reference.provider_league_id
        or provider_league.provider_season_id != reference.provider_season_id
    ):
        raise SetupFinalizationValidationError(
            "The provider returned inconsistent league identity data."
        )

    timestamp = now or datetime.now(UTC)

    try:
        lifecycle = classify_imported_season(
            season_year=provider_league.season_year,
            now=timestamp,
        )
    except FutureSeasonImportError as exc:
        raise SetupFinalizationValidationError(str(exc)) from exc

    league = League(
        installation_id=installation.id,
        provider=connection.provider,
        provider_league_id=(provider_league.provider_league_id),
        sport=provider_league.sport,
        name=provider_league.name,
        logo_url=provider_league.logo_url,
    )

    session.add(league)
    session.flush()

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

    sync_result = synchronize_provider(
        session=session,
        season_id=season.id,
        provider=provider,
        full_sync=True,
        commit_transaction=False,
    )

    season.status = lifecycle.status
    season.activated_at = lifecycle.activated_at
    season.completed_at = lifecycle.completed_at

    installation.active_league_id = league.id
    installation.active_season_id = season.id if lifecycle.synchronization_enabled else None
    installation.status = InstallationStatus.ACTIVE
    installation.setup_completed_at = timestamp

    session.flush()
    session.commit()

    return SetupFinalizationResult(
        installation_id=installation.id,
        league_id=league.id,
        season_id=season.id,
        sync_result=sync_result,
    )
