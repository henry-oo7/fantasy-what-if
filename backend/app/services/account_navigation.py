"""Server-resolved navigation state for the signed-in account (ADR 0010).

The state is derived from the account's own verification and readable
League-Season access only. It never loads League data and never reveals
another user's access.
"""

from enum import StrEnum

from sqlalchemy.orm import Session

from app.auth.identity import IdentityKind
from app.db.auth_models import AuthSessionPurpose, User
from app.repositories.league_access_repository import LeagueAccessRepository
from app.services.league_season_authorization import READABLE_ACCESS_STATUSES


class AccountNavigationState(StrEnum):
    EMAIL_VERIFICATION_REQUIRED = "email_verification_required"
    IMPORT_REQUIRED = "import_required"
    PERSONAL_DASHBOARD = "personal_dashboard"
    # Legacy compatibility, removed with the workspace schema.
    LEGACY_WORKSPACE = "legacy_workspace"
    LEGACY_DELETION_RECOVERY = "legacy_deletion_recovery"


def resolve_navigation_state(
    session: Session,
    *,
    user: User,
    identity: IdentityKind,
    purpose: AuthSessionPurpose,
) -> AccountNavigationState:
    if purpose is AuthSessionPurpose.DELETION_RECOVERY:
        return AccountNavigationState.LEGACY_DELETION_RECOVERY

    if identity is not IdentityKind.PERSONAL:
        return AccountNavigationState.LEGACY_WORKSPACE

    if user.email_verified_at is None:
        return AccountNavigationState.EMAIL_VERIFICATION_REQUIRED

    if LeagueAccessRepository(session).has_access_with_status(
        user_id=user.id, statuses=READABLE_ACCESS_STATUSES
    ):
        return AccountNavigationState.PERSONAL_DASHBOARD

    return AccountNavigationState.IMPORT_REQUIRED
