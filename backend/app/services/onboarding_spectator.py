"""Initial shared spectator setup during provisional onboarding.

Setup happens after successful initial synchronization. It creates the
workspace's single spectator identity and completes onboarding; workspace
activation is a separate, later step.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.platform_enums import OnboardingStage
from app.services.provider_onboarding import (
    ProviderOnboardingError,
    authorize_onboarding_administrator,
)
from app.services.spectator_account import (
    SpectatorAccessState,
    create_workspace_spectator,
    get_workspace_spectator,
    spectator_access_state,
)

READ_STAGES = frozenset({OnboardingStage.SPECTATOR_SETUP_PENDING, OnboardingStage.COMPLETE})


@dataclass(frozen=True, slots=True)
class OnboardingSpectatorState:
    access: SpectatorAccessState
    onboarding_stage: OnboardingStage
    created: bool


def spectator_stage_invalid() -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=409,
        code="onboarding_stage_invalid",
        detail="Shared spectator access cannot be set up at this onboarding step.",
        retryable=False,
    )


def spectator_already_configured() -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=409,
        code="spectator_already_configured",
        detail=(
            "Shared spectator access is already configured. Change the password from "
            "workspace administration."
        ),
        retryable=False,
    )


def _authorize(session: Session, *, user_id: int, workspace_id: int, lock: bool):
    return authorize_onboarding_administrator(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        allowed_stages=READ_STAGES,
        forbidden_code="spectator_setup_forbidden",
        stage_invalid=spectator_stage_invalid,
        lock=lock,
    )


def read_onboarding_spectator(
    session: Session, *, user_id: int, workspace_id: int
) -> OnboardingSpectatorState:
    workspace = _authorize(session, user_id=user_id, workspace_id=workspace_id, lock=False)

    return OnboardingSpectatorState(
        access=spectator_access_state(session, workspace=workspace),
        onboarding_stage=workspace.onboarding_stage or OnboardingStage.SPECTATOR_SETUP_PENDING,
        created=False,
    )


def set_up_onboarding_spectator(
    session: Session, *, user_id: int, workspace_id: int, password: str
) -> OnboardingSpectatorState:
    """Create the spectator and complete onboarding, atomically.

    A repeat after success returns the configured state without touching
    the password; later changes use the administrator management path.
    """

    workspace = _authorize(session, user_id=user_id, workspace_id=workspace_id, lock=True)
    existing = get_workspace_spectator(session, workspace_id=workspace.id, for_update=True)

    if workspace.onboarding_stage is OnboardingStage.COMPLETE:
        if existing is None:
            raise spectator_stage_invalid()

        return OnboardingSpectatorState(
            access=spectator_access_state(session, workspace=workspace),
            onboarding_stage=OnboardingStage.COMPLETE,
            created=False,
        )

    if existing is not None:
        raise spectator_already_configured()

    create_workspace_spectator(session, workspace_id=workspace.id, password=password)
    workspace.onboarding_stage = OnboardingStage.COMPLETE
    session.flush()
    state = OnboardingSpectatorState(
        access=spectator_access_state(session, workspace=workspace),
        onboarding_stage=OnboardingStage.COMPLETE,
        created=True,
    )
    session.commit()

    return state
