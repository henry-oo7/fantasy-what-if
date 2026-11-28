"""Create one independent personal account atomically (ADR 0010).

A personal account has a display name, an email, and a password. It has no
workspace, role, username, claim, connection, League, or Season. The user
and its verification-email intent commit together or not at all.
"""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.auth_models import User
from app.db.platform_enums import RegistrationMode
from app.repositories.auth_repository import (
    AuthenticationRepository,
    DuplicateEmailError,
    normalize_display_name,
)
from app.services.identity_email import queue_registration_verification

# The unique index and legacy column index that can reject a duplicate email.
EMAIL_CONSTRAINTS = frozenset(
    {
        "uq_users_normalized_email",
        "ix_users_email",
        "users_email_key",
    }
)


class RegistrationUnavailableError(RuntimeError):
    """Public registration is not currently available."""


class InvalidDisplayNameError(ValueError):
    """The requested display name is invalid."""


class DuplicateAccountEmailError(RuntimeError):
    """The email already belongs to an account.

    Callers must not disclose this condition. The registration route maps
    it to the same external response as a successful registration.
    """


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """Safe application result. Verification delivery is only queued."""

    user: User


def register_personal_account(
    session: Session,
    *,
    registration_mode: RegistrationMode,
    display_name: str,
    email: str,
    password: str,
) -> RegistrationResult:
    """Create the personal account transaction without committing.

    The user and the verification delivery plus its BackgroundJob are
    written in one savepoint; any failure rolls all of them back. No token
    is created and no provider is called.
    """

    if registration_mode is not RegistrationMode.OPEN:
        raise RegistrationUnavailableError("Registration is unavailable.")

    try:
        normalize_display_name(display_name)
    except ValueError as exc:
        raise InvalidDisplayNameError("Display name cannot be empty.") from exc

    try:
        with session.begin_nested():
            user = AuthenticationRepository(session).create_personal_user(
                display_name=display_name,
                email=email,
                password=password,
            )
            queue_registration_verification(session, user_id=user.id)
            session.flush()
    except DuplicateEmailError as exc:
        raise DuplicateAccountEmailError("The account email is unavailable.") from exc
    except IntegrityError as exc:
        if _constraint_name(exc) in EMAIL_CONSTRAINTS:
            raise DuplicateAccountEmailError("The account email is unavailable.") from exc

        raise

    return RegistrationResult(user=user)


def _constraint_name(error: IntegrityError) -> str | None:
    return getattr(getattr(error.orig, "diag", None), "constraint_name", None)
