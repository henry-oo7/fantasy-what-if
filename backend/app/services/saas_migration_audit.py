"""Read-only audit of the v1.0.1 database before SaaS migration."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.auth_models import AuthSession, User, UserRole
from app.db.models import League, Season, SyncRun
from app.db.platform_enums import FantasyProvider, InstallationStatus
from app.db.platform_models import (
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
    ProviderCredentialKeyringConfigurationError,
)
from app.providers.credential_vault import (
    ProviderCredentialConfigurationError,
    ProviderCredentialDecryptionError,
    ProviderCredentialVault,
)


@dataclass(frozen=True, slots=True)
class AuditCheck:
    """One safe migration-readiness result."""

    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class SaasMigrationAudit:
    """Complete read-only migration audit."""

    checks: tuple[AuditCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def _check(
    checks: list[AuditCheck],
    *,
    name: str,
    passed: bool,
    success: str,
    failure: str,
) -> None:
    checks.append(
        AuditCheck(
            name=name,
            passed=passed,
            message=success if passed else failure,
        )
    )


def audit_saas_migration_readiness(
    session: Session,
    *,
    provider_credential_keyring: ProviderCredentialKeyring,
) -> SaasMigrationAudit:
    """Inspect migration invariants without modifying database state."""

    checks: list[AuditCheck] = []

    installations = session.scalars(select(Installation).order_by(Installation.id)).all()

    _check(
        checks,
        name="installation_count",
        passed=len(installations) == 1,
        success="Exactly one installation exists.",
        failure=(f"Expected exactly one installation, found {len(installations)}."),
    )

    if len(installations) != 1:
        return SaasMigrationAudit(checks=tuple(checks))

    installation = installations[0]

    _check(
        checks,
        name="installation_active",
        passed=installation.status is InstallationStatus.ACTIVE,
        success="The installation is active.",
        failure=("The installation must be active before SaaS migration."),
    )

    _check(
        checks,
        name="setup_completed",
        passed=installation.setup_completed_at is not None,
        success="Installation setup is complete.",
        failure="Installation setup completion is missing.",
    )

    users = session.scalars(select(User).order_by(User.id)).all()
    administrators = [user for user in users if user.role is UserRole.ADMIN]
    spectators = [user for user in users if user.role is UserRole.SPECTATOR]

    _check(
        checks,
        name="administrator_count",
        passed=len(administrators) == 1,
        success="Exactly one administrator exists.",
        failure=(f"Expected exactly one administrator, found {len(administrators)}."),
    )

    _check(
        checks,
        name="spectator_count",
        passed=len(spectators) <= 1,
        success="Zero or one spectator exists.",
        failure=(f"Expected no more than one spectator, found {len(spectators)}."),
    )

    if len(administrators) == 1:
        administrator = administrators[0]
        valid_email = bool(
            administrator.email and administrator.email.strip() and "@" in administrator.email
        )

        _check(
            checks,
            name="administrator_email",
            passed=valid_email,
            success="Administrator email is present.",
            failure=("Administrator email is missing or invalid."),
        )

        _check(
            checks,
            name="administrator_password",
            passed=bool(administrator.password_hash.strip()),
            success="Administrator password hash is present.",
            failure="Administrator password hash is missing.",
        )

    if len(spectators) == 1:
        _check(
            checks,
            name="spectator_password",
            passed=bool(spectators[0].password_hash.strip()),
            success="Spectator password hash is present.",
            failure="Spectator password hash is missing.",
        )

    user_ids = {user.id for user in users}
    invalid_session_count = (
        int(
            session.scalar(
                select(func.count(AuthSession.id)).where(AuthSession.user_id.not_in(user_ids))
            )
            or 0
        )
        if user_ids
        else int(session.scalar(select(func.count(AuthSession.id))) or 0)
    )

    _check(
        checks,
        name="session_ownership",
        passed=invalid_session_count == 0,
        success="Every authentication session has a valid user.",
        failure=(f"Found {invalid_session_count} sessions without a valid user."),
    )

    connections = session.scalars(
        select(ProviderConnection)
        .where(ProviderConnection.installation_id == installation.id)
        .order_by(ProviderConnection.id)
    ).all()
    active_connections = [connection for connection in connections if connection.is_active]

    _check(
        checks,
        name="active_provider_connection",
        passed=len(active_connections) == 1,
        success="Exactly one active provider connection exists.",
        failure=(
            f"Expected exactly one active provider connection, found {len(active_connections)}."
        ),
    )

    active_connection = active_connections[0] if len(active_connections) == 1 else None

    if active_connection is not None:
        _check(
            checks,
            name="provider_is_espn",
            passed=active_connection.provider is FantasyProvider.ESPN,
            success="The active provider is ESPN.",
            failure=("The v1.0.1 production migration expects ESPN."),
        )

        encrypted = active_connection.encrypted_credentials

        _check(
            checks,
            name="encrypted_credentials_present",
            passed=bool(encrypted and encrypted.strip()),
            success="Encrypted provider credentials are present.",
            failure=("Encrypted provider credentials are missing."),
        )

        if encrypted and encrypted.strip() and active_connection.credential_version is not None:
            decryptable = False

            try:
                provider_credential_keyring.key_for_version(active_connection.credential_version)
                vault = ProviderCredentialVault(
                    keys=provider_credential_keyring.keys,
                )
                vault.decrypt(
                    encrypted_credentials=encrypted,
                    key_version=(active_connection.credential_version),
                    expected_provider=(active_connection.provider),
                    expected_schema_version=(active_connection.credential_schema_version),
                )
                decryptable = True
            except (
                ProviderCredentialConfigurationError,
                ProviderCredentialDecryptionError,
                ProviderCredentialKeyringConfigurationError,
            ):
                decryptable = False

        _check(
            checks,
            name="credentials_decryptable",
            passed=decryptable,
            success=("Provider credentials decrypt successfully."),
            failure=("Provider credentials could not be decrypted with the configured key."),
        )

    leagues = session.scalars(
        select(League).where(League.installation_id == installation.id).order_by(League.id)
    ).all()

    _check(
        checks,
        name="league_count",
        passed=len(leagues) == 1,
        success="Exactly one workspace league exists.",
        failure=(f"Expected exactly one workspace league, found {len(leagues)}."),
    )

    claims = session.scalars(
        select(ProviderLeagueClaim).order_by(
            ProviderLeagueClaim.id,
        )
    ).all()

    league_by_id = {league.id: league for league in leagues}
    claim_counts_by_league_id = {league.id: 0 for league in leagues}

    for claim in claims:
        if claim.league_id in claim_counts_by_league_id:
            claim_counts_by_league_id[claim.league_id] += 1

    every_league_claimed_once = len(claims) == len(leagues) and all(
        count == 1 for count in claim_counts_by_league_id.values()
    )

    _check(
        checks,
        name="provider_league_claim_count",
        passed=every_league_claimed_once,
        success=("Every workspace league has exactly one provider identity claim."),
        failure=("One or more workspace leagues are missing a unique provider identity claim."),
    )

    installation_ids = {value.id for value in installations}

    valid_claim_workspaces = all(claim.workspace_id in installation_ids for claim in claims)

    _check(
        checks,
        name="provider_league_claim_workspace",
        passed=valid_claim_workspaces,
        success=("Every provider identity claim references a valid workspace."),
        failure=("One or more provider identity claims reference a missing workspace."),
    )

    no_unattached_claims = all(claim.league_id is not None for claim in claims)

    _check(
        checks,
        name="provider_league_claim_attachment",
        passed=no_unattached_claims,
        success=("No provisional provider identity claims remain."),
        failure=("One or more provider identity claims are not attached to a league."),
    )

    valid_claim_leagues = all(
        claim.league_id is not None and claim.league_id in league_by_id for claim in claims
    )

    _check(
        checks,
        name="provider_league_claim_league",
        passed=valid_claim_leagues,
        success=("Every provider identity claim references a valid league."),
        failure=("One or more provider identity claims reference a missing league."),
    )

    matching_claim_workspaces = all(
        claim.league_id is not None
        and claim.league_id in league_by_id
        and league_by_id[claim.league_id].installation_id == claim.workspace_id
        for claim in claims
    )

    _check(
        checks,
        name="provider_league_claim_workspace_match",
        passed=matching_claim_workspaces,
        success=("Every provider identity claim and league belong to the same workspace."),
        failure=("A provider identity claim and its league belong to different workspaces."),
    )

    matching_claim_providers = all(
        claim.league_id is not None
        and claim.league_id in league_by_id
        and league_by_id[claim.league_id].provider is claim.provider
        for claim in claims
    )

    _check(
        checks,
        name="provider_league_claim_provider_match",
        passed=matching_claim_providers,
        success=("Every provider identity claim matches its league provider."),
        failure=("A provider identity claim does not match its league provider."),
    )

    matching_claim_identities = all(
        claim.league_id is not None
        and claim.league_id in league_by_id
        and (league_by_id[claim.league_id].provider_league_id is not None)
        and (
            claim.normalized_provider_league_id
            == league_by_id[claim.league_id].provider_league_id.strip()
        )
        for claim in claims
    )

    _check(
        checks,
        name="provider_league_claim_identity_match",
        passed=matching_claim_identities,
        success=("Every provider identity claim matches its league identifier."),
        failure=("A provider identity claim does not match its league identifier."),
    )

    active_league = (
        session.get(League, installation.active_league_id)
        if installation.active_league_id is not None
        else None
    )

    _check(
        checks,
        name="active_league_ownership",
        passed=(active_league is not None and active_league.installation_id == installation.id),
        success="The active league belongs to the installation.",
        failure=("The active league is missing or belongs to another installation."),
    )

    active_season = (
        session.get(Season, installation.active_season_id)
        if installation.active_season_id is not None
        else None
    )

    _check(
        checks,
        name="active_season_ownership",
        passed=(
            active_season is not None
            and active_league is not None
            and active_season.league_id == active_league.id
        ),
        success="The active season belongs to the active league.",
        failure=("The active season is missing or does not belong to the active league."),
    )

    if active_season is not None and active_connection is not None:
        _check(
            checks,
            name="season_provider_connection",
            passed=(active_season.provider_connection_id == active_connection.id),
            success=("The active season uses the active provider connection."),
            failure=("The active season does not use the active provider connection."),
        )

    if active_league is not None:
        provider_id = (active_league.provider_league_id or "").strip()

        _check(
            checks,
            name="espn_league_identity",
            passed=(active_league.provider is FantasyProvider.ESPN and provider_id.isdecimal()),
            success="The ESPN league identity is valid.",
            failure=("The active league does not have a valid decimal ESPN league ID."),
        )

    orphaned_sync_runs = int(
        session.scalar(
            select(func.count(SyncRun.id))
            .outerjoin(
                Season,
                SyncRun.season_id == Season.id,
            )
            .where(
                SyncRun.season_id.is_not(None),
                Season.id.is_(None),
            )
        )
        or 0
    )

    _check(
        checks,
        name="sync_run_ownership",
        passed=orphaned_sync_runs == 0,
        success="Synchronization runs reference valid seasons.",
        failure=(f"Found {orphaned_sync_runs} synchronization runs without a valid season."),
    )

    return SaasMigrationAudit(
        checks=tuple(checks),
    )
