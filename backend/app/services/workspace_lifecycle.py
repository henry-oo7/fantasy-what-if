"""Single source of workspace lifecycle rules for authenticated users.

Every caller that decides whether a workspace grants ordinary access,
resumable administrator onboarding, or nothing uses these functions so
the rules cannot drift between endpoints.
"""

from enum import StrEnum

from app.db.auth_models import UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation

# Stages before email verification never authorize an administrator.
_PRE_VERIFICATION_STAGES = frozenset(
    {
        OnboardingStage.REGISTRATION_PENDING,
        OnboardingStage.EMAIL_VERIFICATION_PENDING,
    }
)


class WorkspaceAccess(StrEnum):
    """Stable serialized workspace access for an authenticated user."""

    ACTIVE = "active"
    PROVISIONAL = "provisional"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


def is_active_workspace(workspace: Installation | None) -> bool:
    """Legacy (no registration status) or SaaS-active, and installed."""

    return (
        workspace is not None
        and workspace.status is InstallationStatus.ACTIVE
        and workspace.registration_status in (None, WorkspaceRegistrationStatus.ACTIVE)
    )


def is_deletion_pending(workspace: Installation | None) -> bool:
    """The workspace is inside its FR-20 deletion grace period."""

    return (
        workspace is not None
        and workspace.registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
    )


def is_resumable_onboarding(workspace: Installation | None) -> bool:
    """Verified provisional SaaS workspace an administrator may resume."""

    return (
        workspace is not None
        and workspace.status is not InstallationStatus.ACTIVE
        and workspace.registration_status is WorkspaceRegistrationStatus.PROVISIONAL
        and workspace.onboarding_stage is not None
        and workspace.onboarding_stage not in _PRE_VERIFICATION_STAGES
    )


def resolve_workspace_access(
    workspace: Installation | None,
    role: UserRole | None,
) -> WorkspaceAccess:
    """Map a workspace lifecycle and role to the access a user receives.

    Suspension is reported explicitly. Provisional onboarding is available
    only to administrators; a spectator in any non-active workspace, and
    every deletion-pending, deleted, missing, or inconsistent workspace,
    is simply inactive.
    """

    if workspace is None:
        return WorkspaceAccess.INACTIVE

    if workspace.registration_status is WorkspaceRegistrationStatus.SUSPENDED:
        return WorkspaceAccess.SUSPENDED

    if is_active_workspace(workspace):
        return WorkspaceAccess.ACTIVE

    if role is UserRole.ADMIN and is_resumable_onboarding(workspace):
        return WorkspaceAccess.PROVISIONAL

    return WorkspaceAccess.INACTIVE
