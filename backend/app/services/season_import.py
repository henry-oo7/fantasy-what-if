"""Import another season for an existing fantasy league."""

from __future__ import annotations

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
from app.providers.domain import ProviderCredentials
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


def import_provider_season(
    session: Session,
    *,
    season_year: int,
    credential_key: str,
    registry: ProviderRegistry | None = None,
    now: datetime | None = None,
) -> SeasonImportResult:
    """Import one season into the existing canonical league."""

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

    session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                :lock_key
            )
            """
        ),
        {
            "lock_key": SEASON_IMPORT_LOCK_KEY,
        },
    )

    installations = session.scalars(
        select(Installation).order_by(Installation.id).with_for_update()
    ).all()

    if len(installations) != 1:
        raise SeasonImportUnavailableError(
            "Exactly one installation must exist before importing another season."
        )

    installation = installations[0]

    if installation.status is not InstallationStatus.ACTIVE:
        raise SeasonImportUnavailableError(
            "Another season can be imported only after installation setup is complete."
        )

    leagues = session.scalars(
        select(League)
        .where(
            League.installation_id == installation.id,
        )
        .order_by(League.id)
        .with_for_update()
    ).all()

    if len(leagues) != 1:
        raise SeasonImportUnavailableError("Exactly one installed fantasy league is required.")

    league = leagues[0]

    if league.provider is None or league.provider_league_id is None:
        raise SeasonImportUnavailableError(
            "The installed league does not have canonical provider identity."
        )

    duplicate_season_id = session.scalar(
        select(Season.id)
        .where(
            Season.league_id == league.id,
            Season.year == season_year,
        )
        .limit(1)
    )

    if duplicate_season_id is not None:
        raise SeasonImportConflictError(f"Season {season_year} is already imported.")

    connections = session.scalars(
        select(ProviderConnection)
        .where(
            ProviderConnection.installation_id == installation.id,
            ProviderConnection.provider == league.provider,
            ProviderConnection.is_active.is_(True),
        )
        .order_by(ProviderConnection.id)
        .with_for_update()
    ).all()

    if len(connections) != 1:
        raise SeasonImportUnavailableError("Exactly one active provider connection is required.")

    connection = connections[0]

    if connection.encrypted_credentials is None:
        raise SeasonImportUnavailableError("The provider connection has no stored credentials.")

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

    validation = provider.validate_configuration()

    if not validation.is_valid:
        connection.last_validation_error = validation.safe_message

        raise SeasonImportValidationError(validation.safe_message)

    provider_league = provider.fetch_league(
        provider_league_id=(league.provider_league_id),
        provider_season_id=str(season_year),
    )

    if provider_league.provider_league_id != league.provider_league_id:
        raise SeasonImportValidationError("The provider returned a different league.")

    if (
        provider_league.provider_season_id != str(season_year)
        or provider_league.season_year != season_year
    ):
        raise SeasonImportValidationError("The provider returned a different season.")

    if league.sport is not None and provider_league.sport is not league.sport:
        raise SeasonImportValidationError("The provider season uses a different sport.")

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

    sync_result = synchronize_provider(
        session=session,
        season_id=season.id,
        provider=provider,
        full_sync=True,
        commit_transaction=False,
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
    session.commit()

    return SeasonImportResult(
        installation_id=installation.id,
        league_id=league.id,
        season_id=season.id,
        year=season.year,
        status=lifecycle.status,
        synchronization_enabled=(lifecycle.synchronization_enabled),
        sync_result=sync_result,
    )
