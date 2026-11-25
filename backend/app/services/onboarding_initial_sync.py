"""Initial workspace synchronization for provisional onboarding.

Ownership boundaries:

- The start request only queues work. In one short transaction it locks the
  workspace, re-checks the claim and validated connection, creates or
  returns the single active ``initial_workspace_sync`` job, and sets
  ``initial_sync_running``. It never calls ESPN, decrypts credentials, or
  creates League or Season rows.
- The worker handler loads every input from persisted state, fetches the
  authoritative ESPN league snapshot outside any database transaction,
  creates or reuses the League and Season with the real provider values,
  attaches the claim, and then runs the shared full-season synchronization,
  which prepares, fetches with no transaction, and persists separately.
- The job-success or terminal-failure transaction advances onboarding
  (see ``onboarding_sync_outcome``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db.models import League, Matchup, Season, Team
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    OnboardingStage,
    ProviderAccessMode,
    SeasonStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.providers.base import ProviderAdapter
from app.providers.credential_vault import ProviderCredentialVault
from app.providers.domain import ProviderLeague
from app.providers.espn.client import credentials_from_values
from app.providers.registry import ProviderRegistry
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.provider_league_claim_repository import (
    ProviderLeagueClaimRepository,
    normalize_provider_league_id,
)
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    BackgroundJobSnapshot,
)
from app.services.league_claim import validated_target_from_connection
from app.services.provider_adapter_resolver import build_provider_registry
from app.services.provider_onboarding import (
    ProviderOnboardingError,
    authorize_onboarding_administrator,
)
from app.services.provider_sync import (
    SyncJobAuthority,
    SyncWorkspaceRequirement,
    credential_marker,
    synchronize_provider,
)
from app.services.season_lifecycle import classify_imported_season
from app.services.synchronization_job_contract import (
    synchronization_payload_contains_prohibited_names,
)

INITIAL_SYNC_PAYLOAD_VERSION = 1
INITIAL_SYNC_PRIORITY = 50
INITIAL_SYNC_MAXIMUM_ATTEMPTS = 3
INITIAL_SYNC_TRIGGER = "onboarding"

START_STAGES = frozenset(
    {
        OnboardingStage.INITIAL_SYNC_PENDING,
        OnboardingStage.INITIAL_SYNC_FAILED,
        # A repeated start returns the active job instead of failing.
        OnboardingStage.INITIAL_SYNC_RUNNING,
    }
)
PROGRESS_STAGES = START_STAGES | {OnboardingStage.SPECTATOR_SETUP_PENDING}


def has_required_core_data(session: Session, season_id: int) -> bool:
    """Single rule for "core league data exists": at least two teams and one matchup.

    Shared by the initial-sync handler and workspace activation.
    """

    team_count = int(
        session.scalar(select(func.count()).select_from(Team).where(Team.season_id == season_id))
        or 0
    )
    matchup_count = int(
        session.scalar(
            select(func.count()).select_from(Matchup).where(Matchup.season_id == season_id)
        )
        or 0
    )

    return team_count >= 2 and matchup_count >= 1


def initial_sync_idempotency_key(workspace_id: int) -> str:
    return f"onboarding-initial-sync:{workspace_id}"


def sync_stage_invalid() -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=409,
        code="onboarding_stage_invalid",
        detail="Initial synchronization is not available at this onboarding step.",
        retryable=False,
    )


def sync_target_unavailable() -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=409,
        code="provider_target_unavailable",
        detail="The connected league is unavailable. Contact support before trying again.",
        retryable=False,
    )


def _authorize(
    session: Session,
    *,
    user_id: int,
    workspace_id: int,
    stages: frozenset[OnboardingStage],
    lock: bool,
) -> Installation:
    return authorize_onboarding_administrator(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        allowed_stages=stages,
        forbidden_code="initial_sync_forbidden",
        stage_invalid=sync_stage_invalid,
        lock=lock,
    )


def _active_connection(session: Session, workspace_id: int) -> ProviderConnection | None:
    return session.scalar(
        select(ProviderConnection).where(
            ProviderConnection.installation_id == workspace_id,
            ProviderConnection.is_active.is_(True),
        )
    )


def _claim(session: Session, workspace_id: int) -> ProviderLeagueClaim | None:
    return session.scalar(
        select(ProviderLeagueClaim).where(ProviderLeagueClaim.workspace_id == workspace_id)
    )


def _claim_matches(claim: ProviderLeagueClaim | None, connection: ProviderConnection) -> bool:
    return (
        claim is not None
        and connection.provider_league_id is not None
        and claim.provider is connection.provider
        and claim.normalized_provider_league_id
        == normalize_provider_league_id(connection.provider_league_id)
    )


# --------------------------------------------------------------------------
# Safe job view
# --------------------------------------------------------------------------

_SAFE_FAILURE_DETAILS = {
    "authentication": (
        "ESPN no longer accepts this league's saved access. Try again, and contact "
        "support if it keeps failing."
    ),
    "authorization": (
        "ESPN no longer accepts this league's saved access. Try again, and contact "
        "support if it keeps failing."
    ),
    "unavailable": "ESPN was unavailable during the first import. Try again in a few minutes.",
    "rate_limit": "ESPN was unavailable during the first import. Try again in a few minutes.",
    "worker_lease_expired": "The first import was interrupted. Try again.",
}
_GENERIC_FAILURE_DETAIL = "The first import did not finish. Try again."
_STATUS_DETAILS = {
    BackgroundJobStatus.PENDING: "The first import is queued.",
    BackgroundJobStatus.RUNNING: "The first import is running. This can take several minutes.",
    BackgroundJobStatus.RETRY_SCHEDULED: (
        "ESPN did not finish responding. The first import will retry automatically."
    ),
    BackgroundJobStatus.SUCCEEDED: "Your league data is ready.",
    BackgroundJobStatus.CANCELED: _GENERIC_FAILURE_DETAIL,
}


@dataclass(frozen=True, slots=True)
class InitialSyncJobView:
    """Safe, workspace-scoped job fields. No payload, lease or raw errors."""

    id: int
    status: BackgroundJobStatus
    retryable: bool
    detail: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None


@dataclass(frozen=True, slots=True)
class InitialSyncProgress:
    onboarding_stage: OnboardingStage
    job: InitialSyncJobView | None


def _job_view(job: BackgroundJob) -> InitialSyncJobView:
    if job.status is BackgroundJobStatus.FAILED:
        detail = _SAFE_FAILURE_DETAILS.get(job.safe_error_code or "", _GENERIC_FAILURE_DETAIL)
    else:
        detail = _STATUS_DETAILS[job.status]

    return InitialSyncJobView(
        id=job.id,
        status=job.status,
        # An administrator may always retry a terminal onboarding failure.
        retryable=job.status in (BackgroundJobStatus.FAILED, BackgroundJobStatus.CANCELED),
        detail=detail,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failed_at=job.failed_at,
    )


# --------------------------------------------------------------------------
# Start and progress
# --------------------------------------------------------------------------


def start_initial_sync(session: Session, *, user_id: int, workspace_id: int) -> InitialSyncProgress:
    """Queue or return the single active initial sync job, then commit."""

    workspace = _authorize(
        session, user_id=user_id, workspace_id=workspace_id, stages=START_STAGES, lock=True
    )
    connection = _active_connection(session, workspace.id)

    try:
        validated_target_from_connection(connection)
    except ProviderOnboardingError as exc:
        raise sync_target_unavailable() from exc

    if connection is None or not _claim_matches(_claim(session, workspace.id), connection):
        raise sync_target_unavailable()

    result = BackgroundJobRepository(session).enqueue(
        workspace_id=workspace.id,
        job_type=BackgroundJobType.INITIAL_WORKSPACE_SYNC,
        # Server-side lookup only: no target, credentials or client data.
        payload={"trigger": INITIAL_SYNC_TRIGGER},
        idempotency_key=initial_sync_idempotency_key(workspace.id),
        payload_version=INITIAL_SYNC_PAYLOAD_VERSION,
        priority=INITIAL_SYNC_PRIORITY,
        maximum_attempts=INITIAL_SYNC_MAXIMUM_ATTEMPTS,
    )
    workspace.onboarding_stage = OnboardingStage.INITIAL_SYNC_RUNNING
    session.flush()
    view = _job_view(result.job)
    session.commit()

    return InitialSyncProgress(onboarding_stage=OnboardingStage.INITIAL_SYNC_RUNNING, job=view)


def read_initial_sync_progress(
    session: Session, *, user_id: int, workspace_id: int
) -> InitialSyncProgress:
    """Latest initial sync job for the caller's own workspace only."""

    workspace = _authorize(
        session, user_id=user_id, workspace_id=workspace_id, stages=PROGRESS_STAGES, lock=False
    )
    job = session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.workspace_id == workspace.id,
            BackgroundJob.job_type == BackgroundJobType.INITIAL_WORKSPACE_SYNC,
        )
        .order_by(BackgroundJob.id.desc())
        .limit(1)
    )

    if workspace.onboarding_stage is None:
        raise sync_stage_invalid()

    return InitialSyncProgress(
        onboarding_stage=workspace.onboarding_stage,
        job=_job_view(job) if job is not None else None,
    )


# --------------------------------------------------------------------------
# Worker handler
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SyncContext:
    connection_id: int
    league_id: str
    season_year: int
    swid: str | None
    espn_s2: str | None
    credential_marker: str

    def __repr__(self) -> str:
        return (
            f"_SyncContext(connection_id={self.connection_id}, league_id=<target>, "
            "credentials=<redacted>)"
        )


def _job_error(code: str, message: str, *, retryable: bool = False) -> BackgroundJobExecutionError:
    return BackgroundJobExecutionError(
        safe_error_code=code,
        safe_error_message=message,
        retryable=retryable,
    )


class InitialWorkspaceSyncHandler:
    """Execute one provisional workspace's first ESPN import."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        registry: ProviderRegistry | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.registry = registry
        self.now = now

    def __call__(self, job: BackgroundJobSnapshot) -> None:
        self._validate_payload(job)
        context = self._load_context(job.workspace_id)
        provider = self._build_provider(context)

        # Authoritative snapshot, fetched with no database session open.
        snapshot = provider.fetch_league(
            provider_league_id=context.league_id,
            provider_season_id=str(context.season_year),
        )
        self._verify_snapshot(snapshot, context)

        season_id = self._persist_identity(job.workspace_id, context, snapshot)

        # Shared full-season synchronization: short preparation transaction,
        # provider fetch with no transaction, separate persistence transaction.
        synchronize_provider(
            session_factory=self.session_factory,
            season_id=season_id,
            provider=provider,
            full_sync=True,
            requirement=SyncWorkspaceRequirement.INITIAL_ONBOARDING,
            job_authority=SyncJobAuthority(job_id=job.id, attempt_count=job.attempt_count),
            expected_credential_marker=context.credential_marker,
        )

        self._verify_and_classify(season_id, snapshot.season_year)

    @staticmethod
    def _validate_payload(job: BackgroundJobSnapshot) -> None:
        payload = dict(job.payload)

        if (
            job.payload_version != INITIAL_SYNC_PAYLOAD_VERSION
            or set(payload) - {"trigger"}
            or synchronization_payload_contains_prohibited_names(payload)
        ):
            raise _job_error("invalid_job_payload", "The background job payload is invalid.")

    def _load_context(self, workspace_id: int) -> _SyncContext:
        with self.session_factory() as session:
            workspace = session.get(Installation, workspace_id)

            if (
                workspace is None
                or workspace.registration_status is not WorkspaceRegistrationStatus.PROVISIONAL
                or workspace.onboarding_stage is not OnboardingStage.INITIAL_SYNC_RUNNING
            ):
                raise _job_error(
                    "onboarding_state_invalid",
                    "The workspace is not waiting for its first import.",
                )

            connection = _active_connection(session, workspace_id)

            try:
                target = validated_target_from_connection(connection)
            except ProviderOnboardingError as exc:
                raise _job_error(
                    "provider_target_unavailable", "The connected league is unavailable."
                ) from exc

            if connection is None or not _claim_matches(_claim(session, workspace_id), connection):
                raise _job_error(
                    "provider_target_unavailable", "The connected league is unavailable."
                )

            swid: str | None = None
            espn_s2: str | None = None

            # Private credentials are decrypted only here, inside the worker.
            if target.access_mode is ProviderAccessMode.PRIVATE:
                swid, espn_s2 = self._decrypt(connection)

            return _SyncContext(
                connection_id=connection.id,
                league_id=target.league_id,
                season_year=target.season_year,
                swid=swid,
                espn_s2=espn_s2,
                credential_marker=credential_marker(connection),
            )

    def _decrypt(self, connection: ProviderConnection) -> tuple[str, str]:
        unavailable = _job_error(
            "provider_credentials_unavailable",
            "The saved league access could not be used.",
        )

        try:
            keyring = self.settings.provider_credential_keyring()

            if (
                not keyring.configured
                or connection.encrypted_credentials is None
                or connection.credential_version is None
            ):
                raise unavailable

            envelope = ProviderCredentialVault(keys=keyring.keys).decrypt(
                encrypted_credentials=connection.encrypted_credentials,
                key_version=connection.credential_version,
                expected_provider=connection.provider,
                expected_schema_version=connection.credential_schema_version,
            )
        except BackgroundJobExecutionError:
            raise
        except Exception as exc:
            raise unavailable from exc

        values = dict(envelope.credentials.values)
        swid = values.get("swid")
        espn_s2 = values.get("espn_s2")

        if not swid or not espn_s2:
            raise unavailable

        return swid, espn_s2

    def _build_provider(self, context: _SyncContext) -> ProviderAdapter:
        credentials = credentials_from_values(
            league_id=context.league_id,
            season=context.season_year,
            espn_s2=context.espn_s2,
            swid=context.swid,
        )
        registry = self.registry if self.registry is not None else build_provider_registry()

        return registry.create(FantasyProvider.ESPN, credentials)

    @staticmethod
    def _verify_snapshot(snapshot: ProviderLeague, context: _SyncContext) -> None:
        if (
            normalize_provider_league_id(snapshot.provider_league_id)
            != normalize_provider_league_id(context.league_id)
            or snapshot.season_year != context.season_year
        ):
            raise _job_error(
                "provider_identity_mismatch",
                "ESPN returned a different league or season than the connected league.",
            )

    def _persist_identity(
        self, workspace_id: int, context: _SyncContext, snapshot: ProviderLeague
    ) -> int:
        """Create or reuse League and Season with real provider values, then commit."""

        with self.session_factory() as session:
            workspace = session.scalar(
                select(Installation).where(Installation.id == workspace_id).with_for_update()
            )

            if (
                workspace is None
                or workspace.onboarding_stage is not OnboardingStage.INITIAL_SYNC_RUNNING
            ):
                raise _job_error(
                    "onboarding_state_invalid",
                    "The workspace is not waiting for its first import.",
                )

            claim = session.scalar(
                select(ProviderLeagueClaim)
                .where(ProviderLeagueClaim.workspace_id == workspace_id)
                .with_for_update()
            )
            connection = session.get(ProviderConnection, context.connection_id)

            if claim is None or connection is None or not connection.is_active:
                raise _job_error(
                    "provider_target_unavailable", "The connected league is unavailable."
                )

            league = session.get(League, claim.league_id) if claim.league_id is not None else None

            if league is not None and league.installation_id != workspace_id:
                raise _job_error(
                    "workspace_ownership_mismatch",
                    "The connected league does not belong to this workspace.",
                )

            if league is None:
                league = League(
                    installation_id=workspace_id,
                    provider=connection.provider,
                    provider_league_id=claim.normalized_provider_league_id,
                )
                session.add(league)

            league.sport = snapshot.sport
            league.name = snapshot.name
            league.logo_url = snapshot.logo_url
            session.flush()

            ProviderLeagueClaimRepository(session).attach_league(claim, league_id=league.id)

            season = session.scalar(
                select(Season).where(
                    Season.league_id == league.id,
                    Season.year == snapshot.season_year,
                )
            )

            if season is None:
                season = Season(
                    league_id=league.id,
                    year=snapshot.season_year,
                    status=SeasonStatus.DRAFT,
                )
                session.add(season)

            season.provider_connection_id = connection.id
            season.provider_season_id = snapshot.provider_season_id
            season.current_week = snapshot.current_week
            season.regular_season_weeks = snapshot.regular_season_weeks
            season.team_count = len(snapshot.teams)
            season.playoff_week_start = snapshot.playoff_week_start
            season.playoff_week_end = snapshot.playoff_week_end
            season.scoring_type = snapshot.scoring_type
            session.flush()
            season_id = season.id
            session.commit()

            return season_id

    def _verify_and_classify(self, season_id: int, season_year: int) -> None:
        """Require core imported data; record the season lifecycle. No activation."""

        with self.session_factory() as session:
            season = session.get(Season, season_id)

            if season is None or not has_required_core_data(session, season_id):
                raise _job_error(
                    "initial_sync_incomplete",
                    "The first import did not produce the required league data.",
                )

            lifecycle = classify_imported_season(
                season_year=season_year, now=self.now or datetime.now(UTC)
            )
            season.status = lifecycle.status
            season.activated_at = lifecycle.activated_at
            season.completed_at = lifecycle.completed_at
            session.commit()
