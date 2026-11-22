"""Create one provisional SaaS workspace and administrator atomically."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_hex

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.platform_enums import (
    InstallationStatus,
    OnboardingStage,
    RegistrationMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation
from app.repositories.auth_repository import (
    AuthenticationRepository,
    DuplicateEmailError,
    DuplicateUsernameError,
    normalize_display_name,
    normalize_email,
)
from app.services.email_verification import (
    IssuedEmailVerification,
    issue_email_verification_token,
)

WORKSPACE_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WORKSPACE_SLUG_MINIMUM_LENGTH = 3
WORKSPACE_SLUG_MAXIMUM_LENGTH = 50

RESERVED_WORKSPACE_SLUGS = frozenset(
    {
        "admin",
        "administrator",
        "api",
        "app",
        "auth",
        "billing",
        "help",
        "login",
        "logout",
        "operator",
        "register",
        "registration",
        "security",
        "setup",
        "spectator",
        "status",
        "support",
        "system",
        "www",
    }
)

WORKSPACE_SLUG_CONSTRAINT = "uq_installations_normalized_slug"
ADMIN_EMAIL_CONSTRAINTS = frozenset(
    {
        "uq_users_admin_normalized_email",
        "ix_users_email",
        "users_email_key",
    }
)
USERNAME_CONSTRAINTS = frozenset(
    {
        "ix_users_username",
        "users_username_key",
    }
)


class RegistrationUnavailableError(RuntimeError):
    """Public registration is not currently available."""


class RegistrationConflictError(ValueError):
    """A public registration identity is unavailable."""


class InvalidWorkspaceSlugError(ValueError):
    """The requested workspace slug is invalid."""


class InvalidWorkspaceNameError(ValueError):
    """The requested workspace display name is invalid."""


class InvalidAdministratorDisplayNameError(ValueError):
    """The requested administrator display name is invalid."""


class DuplicateAdministratorEmailError(RuntimeError):
    """The administrator email already belongs to an account.

    Callers must not disclose this condition. The registration route maps
    it to the same external response as a successful registration.
    """


@dataclass(frozen=True, slots=True)
class ProvisionalRegistrationResult:
    """Safe application result plus the email-delivery token."""

    workspace: Installation
    administrator: User
    verification: IssuedEmailVerification


def register_provisional_administrator(
    session: Session,
    *,
    registration_mode: RegistrationMode,
    workspace_name: str,
    workspace_slug: str,
    administrator_display_name: str,
    administrator_email: str,
    administrator_password: str,
    verification_lifetime_hours: int,
    now: datetime | None = None,
) -> ProvisionalRegistrationResult:
    """Create the provisional identity transaction without committing."""

    if registration_mode is not RegistrationMode.OPEN:
        raise RegistrationUnavailableError("Registration is unavailable.")

    normalized_workspace_name = normalize_workspace_name(workspace_name)
    normalized_slug = normalize_workspace_slug(workspace_slug)
    normalized_email = normalize_email(administrator_email)
    try:
        normalized_display_name = normalize_display_name(administrator_display_name)
    except ValueError as exc:
        raise InvalidAdministratorDisplayNameError("Display name cannot be empty.") from exc

    current_time = now or datetime.now(UTC)

    try:
        with session.begin_nested():
            workspace = Installation(
                application_name=normalized_workspace_name,
                slug=normalized_slug,
                normalized_slug=normalized_slug,
                timezone="America/Chicago",
                status=InstallationStatus.SETUP_IN_PROGRESS,
                registration_status=(WorkspaceRegistrationStatus.PROVISIONAL),
                onboarding_stage=(OnboardingStage.EMAIL_VERIFICATION_PENDING),
                setup_started_at=current_time,
            )
            session.add(workspace)
            session.flush()

            administrator = AuthenticationRepository(session).create_user(
                username=generate_internal_admin_username(),
                display_name=normalized_display_name,
                email=normalized_email,
                password=administrator_password,
                role=UserRole.ADMIN,
                is_active=True,
                workspace_id=workspace.id,
            )

            verification = issue_email_verification_token(
                session,
                user=administrator,
                lifetime_hours=verification_lifetime_hours,
                now=current_time,
            )

            session.flush()

    except DuplicateEmailError as exc:
        raise DuplicateAdministratorEmailError("Administrator email is unavailable.") from exc
    except DuplicateUsernameError as exc:
        raise RegistrationConflictError("The registration information is unavailable.") from exc
    except IntegrityError as exc:
        constraint_name = _constraint_name(exc)

        if constraint_name == WORKSPACE_SLUG_CONSTRAINT:
            raise RegistrationConflictError("That workspace slug is unavailable.") from exc

        if constraint_name in ADMIN_EMAIL_CONSTRAINTS:
            raise DuplicateAdministratorEmailError("Administrator email is unavailable.") from exc

        if constraint_name in USERNAME_CONSTRAINTS:
            raise RegistrationConflictError("The registration information is unavailable.") from exc

        raise

    return ProvisionalRegistrationResult(
        workspace=workspace,
        administrator=administrator,
        verification=verification,
    )


def normalize_workspace_name(
    workspace_name: str,
) -> str:
    normalized = " ".join(workspace_name.strip().split())

    if not normalized:
        raise InvalidWorkspaceNameError("Workspace name cannot be empty.")

    if len(normalized) > 120:
        raise InvalidWorkspaceNameError("Workspace name cannot exceed 120 characters.")

    return normalized


def normalize_workspace_slug(
    workspace_slug: str,
) -> str:
    normalized = workspace_slug.strip().casefold()

    if not (WORKSPACE_SLUG_MINIMUM_LENGTH <= len(normalized) <= WORKSPACE_SLUG_MAXIMUM_LENGTH):
        raise InvalidWorkspaceSlugError(
            "Workspace slug must contain between "
            f"{WORKSPACE_SLUG_MINIMUM_LENGTH} and "
            f"{WORKSPACE_SLUG_MAXIMUM_LENGTH} characters."
        )

    if not WORKSPACE_SLUG_PATTERN.fullmatch(normalized):
        raise InvalidWorkspaceSlugError(
            "Workspace slug may contain lowercase letters, "
            "numbers, and single hyphens between segments."
        )

    if normalized in RESERVED_WORKSPACE_SLUGS:
        raise InvalidWorkspaceSlugError("That workspace slug is reserved.")

    return normalized


def generate_internal_admin_username() -> str:
    """Return a nonpublic compatibility value for users.username."""

    return f"admin-{token_hex(16)}"


def _constraint_name(
    error: IntegrityError,
) -> str | None:
    diagnostic = getattr(
        error.orig,
        "diag",
        None,
    )

    return getattr(
        diagnostic,
        "constraint_name",
        None,
    )
