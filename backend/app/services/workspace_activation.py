"""Final activation gate for a completed provisional workspace.

``onboarding_stage = complete`` is necessary but never sufficient: the
persisted state is rechecked independently before activation. Activation
performs no provider I/O, decryption, synchronization or job work, and
creates or repairs no domain rows.

Transaction boundary: one short transaction that locks the workspace, the
active provider connection and the claim, evaluates every invariant, and
then sets the active selection and lifecycle fields. A failure changes
nothing; a repeat after success returns the active state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    InstallationStatus,
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
from app.repositories.provider_league_claim_repository import normalize_provider_league_id
from app.services.league_claim import validated_target_from_connection
from app.services.onboarding_initial_sync import has_required_core_data
from app.services.provider_onboarding import (
    ProviderOnboardingError,
    authorize_onboarding_administrator,
)
from app.services.spectator_account import get_workspace_spectator
from app.services.workspace_lifecycle import is_active_workspace

ACTIVATION_STAGES = frozenset({OnboardingStage.COMPLETE})
_IMPORTED_SEASON_STATUSES = frozenset({SeasonStatus.ACTIVE, SeasonStatus.COMPLETED})


class ActivationCategory(StrEnum):
    """Safe corrective category for an activation that is not ready."""

    PROVIDER_CONNECTION = "provider_connection"
    SYNCHRONIZATION = "synchronization"
    LEAGUE_DATA = "league_data"
    SPECTATOR_ACCESS = "spectator_access"


ACTIVATION_DETAILS = {
    ActivationCategory.PROVIDER_CONNECTION: "The connected league is unavailable.",
    ActivationCategory.SYNCHRONIZATION: "Initial synchronization has not completed.",
    ActivationCategory.LEAGUE_DATA: "The imported league data is incomplete.",
    ActivationCategory.SPECTATOR_ACCESS: "Shared spectator access is not configured and enabled.",
}


class ActivationNotReadyError(Exception):
    def __init__(self, category: ActivationCategory) -> None:
        super().__init__(category.value)
        self.category = category
        self.detail = ACTIVATION_DETAILS[category]


@dataclass(frozen=True, slots=True)
class ActivationReadiness:
    """Safe review data. No IDs other than the public slug and League ID."""

    workspace_slug: str | None
    provider: str | None
    league_id: str | None
    season_year: int | None
    access_mode: ProviderAccessMode | None
    synchronization_complete: bool
    spectator_access_ready: bool
    blocking_category: ActivationCategory | None


@dataclass(frozen=True, slots=True)
class _Verified:
    league_id: int
    season_id: int
    season_status: SeasonStatus


@dataclass(frozen=True, slots=True)
class ActivationResult:
    workspace_slug: str | None
    already_active: bool


def activation_stage_invalid() -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=409,
        code="onboarding_stage_invalid",
        detail="The workspace cannot be activated at this onboarding step.",
        retryable=False,
    )


def _authorize(session: Session, *, user_id: int, workspace_id: int, lock: bool) -> Installation:
    return authorize_onboarding_administrator(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        allowed_stages=ACTIVATION_STAGES,
        forbidden_code="activation_forbidden",
        stage_invalid=activation_stage_invalid,
        lock=lock,
    )


def _spectator_ready(session: Session, workspace_id: int) -> bool:
    spectator = get_workspace_spectator(session, workspace_id=workspace_id)

    return (
        spectator is not None
        and spectator.is_active
        and spectator.email is None
        and bool(spectator.password_hash)
    )


def _evaluate(session: Session, workspace: Installation, *, lock: bool) -> _Verified:
    """Every persisted invariant, in corrective order. Raises on the first failure."""

    connection_query = select(ProviderConnection).where(
        ProviderConnection.installation_id == workspace.id,
        ProviderConnection.is_active.is_(True),
    )
    claim_query = select(ProviderLeagueClaim).where(
        ProviderLeagueClaim.workspace_id == workspace.id
    )

    if lock:
        connection_query = connection_query.with_for_update()
        claim_query = claim_query.with_for_update()

    connection = session.scalar(connection_query)

    try:
        target = validated_target_from_connection(connection)
    except ProviderOnboardingError as exc:
        raise ActivationNotReadyError(ActivationCategory.PROVIDER_CONNECTION) from exc

    claim = session.scalar(claim_query)

    if (
        connection is None
        or claim is None
        or claim.provider is not connection.provider
        or claim.normalized_provider_league_id != normalize_provider_league_id(target.league_id)
    ):
        raise ActivationNotReadyError(ActivationCategory.PROVIDER_CONNECTION)

    latest_job = session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.workspace_id == workspace.id,
            BackgroundJob.job_type == BackgroundJobType.INITIAL_WORKSPACE_SYNC,
        )
        .order_by(BackgroundJob.id.desc())
        .limit(1)
    )

    if latest_job is None or latest_job.status is not BackgroundJobStatus.SUCCEEDED:
        raise ActivationNotReadyError(ActivationCategory.SYNCHRONIZATION)

    league = session.get(League, claim.league_id) if claim.league_id is not None else None

    if (
        league is None
        or league.installation_id != workspace.id
        or league.provider is not claim.provider
        or league.provider_league_id != claim.normalized_provider_league_id
    ):
        raise ActivationNotReadyError(ActivationCategory.LEAGUE_DATA)

    season = session.scalar(
        select(Season).where(Season.league_id == league.id, Season.year == target.season_year)
    )

    if (
        season is None
        or season.provider_connection_id != connection.id
        or season.status not in _IMPORTED_SEASON_STATUSES
        or not has_required_core_data(session, season.id)
    ):
        raise ActivationNotReadyError(ActivationCategory.LEAGUE_DATA)

    if workspace.active_league_id not in (None, league.id) or workspace.active_season_id not in (
        None,
        season.id,
    ):
        raise ActivationNotReadyError(ActivationCategory.LEAGUE_DATA)

    if not _spectator_ready(session, workspace.id):
        raise ActivationNotReadyError(ActivationCategory.SPECTATOR_ACCESS)

    assert season.status is not None

    return _Verified(league_id=league.id, season_id=season.id, season_status=season.status)


def read_activation_readiness(
    session: Session, *, user_id: int, workspace_id: int
) -> ActivationReadiness:
    workspace = _authorize(session, user_id=user_id, workspace_id=workspace_id, lock=False)
    connection = session.scalar(
        select(ProviderConnection).where(
            ProviderConnection.installation_id == workspace.id,
            ProviderConnection.is_active.is_(True),
        )
    )
    latest_job = session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.workspace_id == workspace.id,
            BackgroundJob.job_type == BackgroundJobType.INITIAL_WORKSPACE_SYNC,
        )
        .order_by(BackgroundJob.id.desc())
        .limit(1)
    )

    blocking: ActivationCategory | None = None

    try:
        _evaluate(session, workspace, lock=False)
    except ActivationNotReadyError as exc:
        blocking = exc.category

    return ActivationReadiness(
        workspace_slug=workspace.slug,
        provider=connection.provider.value if connection is not None else None,
        league_id=connection.provider_league_id if connection is not None else None,
        season_year=connection.provider_season_year if connection is not None else None,
        access_mode=connection.access_mode if connection is not None else None,
        synchronization_complete=(
            latest_job is not None and latest_job.status is BackgroundJobStatus.SUCCEEDED
        ),
        spectator_access_ready=_spectator_ready(session, workspace.id),
        blocking_category=blocking,
    )


def activate_workspace(
    session: Session, *, user_id: int, workspace_id: int, now: datetime | None = None
) -> ActivationResult:
    """Provisional-to-active transition. The caller owns the session; this commits."""

    workspace = session.scalar(
        select(Installation).where(Installation.id == workspace_id).with_for_update()
    )
    user = session.get(User, user_id)

    # Idempotent repeat: the same verified administrator of an already active workspace.
    if (
        workspace is not None
        and is_active_workspace(workspace)
        and workspace.onboarding_stage is OnboardingStage.COMPLETE
        and user is not None
        and user.workspace_id == workspace.id
        and user.role is UserRole.ADMIN
        and user.email_verified_at is not None
        and user.is_active
    ):
        session.rollback()

        return ActivationResult(workspace_slug=workspace.slug, already_active=True)

    workspace = _authorize(session, user_id=user_id, workspace_id=workspace_id, lock=True)
    verified = _evaluate(session, workspace, lock=True)

    workspace.active_league_id = verified.league_id
    # Existing convention: only a synchronization-enabled season is active;
    # a completed historical season is viewed through the stored-data fallback.
    workspace.active_season_id = (
        verified.season_id if verified.season_status is SeasonStatus.ACTIVE else None
    )
    workspace.registration_status = WorkspaceRegistrationStatus.ACTIVE
    workspace.status = InstallationStatus.ACTIVE

    if workspace.setup_completed_at is None:
        workspace.setup_completed_at = now or datetime.now(UTC)

    session.flush()
    result = ActivationResult(workspace_slug=workspace.slug, already_active=False)
    session.commit()

    return result
