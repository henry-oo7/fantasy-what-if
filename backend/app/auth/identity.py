"""Explicit classification of the three transitional user shapes (ADR 0010).

A personal account has no workspace, role, or username. Legacy
administrators and the shared legacy spectator keep the workspace model
until ``chore/retire-workspace-schema``. Callers classify with this module
rather than inferring identity from one nullable column; a row matching no
shape is invalid and must not authenticate.
"""

from enum import StrEnum

from app.db.auth_models import User, UserRole


class IdentityKind(StrEnum):
    PERSONAL = "personal"
    LEGACY_ADMINISTRATOR = "legacy_administrator"
    LEGACY_SPECTATOR = "legacy_spectator"


def classify_identity(user: User) -> IdentityKind | None:
    """Return the user's identity shape, or ``None`` for an invalid row."""

    if user.workspace_id is None:
        if user.role is None and user.username is None:
            return IdentityKind.PERSONAL

        return None

    if user.role is UserRole.ADMIN:
        return IdentityKind.LEGACY_ADMINISTRATOR

    if user.role is UserRole.SPECTATOR:
        return IdentityKind.LEGACY_SPECTATOR

    return None


def can_log_in_by_email(user: User) -> bool:
    """Email login admits personal accounts and legacy administrators only."""

    return classify_identity(user) in (
        IdentityKind.PERSONAL,
        IdentityKind.LEGACY_ADMINISTRATOR,
    )
