"""Manage each workspace's single shared spectator identity.

Product model (ADR 0005): exactly one shared spectator per workspace,
created and managed by the administrator. Spectators sign in with the
workspace slug plus the shared password. They never register, use email,
choose a username, recover a password, or receive individual accounts.

``users.username`` is required by the schema, so the spectator receives
a generated internal compatibility value that is never returned by the
modern API or shown in the interface.

Every operation is scoped to one workspace and serialized by locking that
workspace row. ``uq_users_one_spectator_per_workspace`` remains the
authoritative guard against a second spectator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_hex

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import PASSWORD_MAX_LENGTH, hash_password
from app.db import model_registry  # noqa: F401
from app.db.auth_models import SessionRevocationReason, User, UserRole
from app.db.platform_models import Installation
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository

SPECTATOR_PASSWORD_MIN_LENGTH = 14
SPECTATOR_PASSWORD_MAX_LENGTH = PASSWORD_MAX_LENGTH
SPECTATOR_DISPLAY_NAME = "League spectator"
ONE_SPECTATOR_CONSTRAINT = "uq_users_one_spectator_per_workspace"


class SpectatorAccountError(RuntimeError):
    """Base error for shared spectator management."""


class SpectatorAlreadyExistsError(SpectatorAccountError):
    """The workspace already has its spectator identity."""


class SpectatorNotFoundError(SpectatorAccountError):
    """The workspace has no spectator identity."""


class SpectatorWorkspaceNotFoundError(SpectatorAccountError):
    """The workspace does not exist."""


class SpectatorPasswordError(ValueError):
    """The shared password violates the length policy."""


@dataclass(frozen=True, slots=True)
class SpectatorAccessState:
    """Safe state only: never a password, hash, username, email or session."""

    workspace_slug: str | None
    configured: bool
    enabled: bool


def validate_spectator_password(password: str) -> None:
    """Length policy only. The value is never trimmed or normalized."""

    if len(password) < SPECTATOR_PASSWORD_MIN_LENGTH:
        raise SpectatorPasswordError(
            f"The shared spectator password must contain at least "
            f"{SPECTATOR_PASSWORD_MIN_LENGTH} characters."
        )

    if len(password) > SPECTATOR_PASSWORD_MAX_LENGTH:
        raise SpectatorPasswordError(
            f"The shared spectator password cannot contain more than "
            f"{SPECTATOR_PASSWORD_MAX_LENGTH} characters."
        )


def generate_internal_spectator_username() -> str:
    """Nonpublic compatibility value for users.username."""

    return f"spectator-{token_hex(16)}"


def lock_workspace(session: Session, workspace_id: int) -> Installation:
    workspace = session.scalar(
        select(Installation).where(Installation.id == workspace_id).with_for_update()
    )

    if workspace is None:
        raise SpectatorWorkspaceNotFoundError("The workspace is unavailable.")

    return workspace


def get_workspace_spectator(
    session: Session,
    *,
    workspace_id: int,
    for_update: bool = False,
) -> User | None:
    statement = select(User).where(
        User.workspace_id == workspace_id,
        User.role == UserRole.SPECTATOR,
    )

    if for_update:
        statement = statement.with_for_update()

    return session.scalar(statement)


def spectator_access_state(session: Session, *, workspace: Installation) -> SpectatorAccessState:
    spectator = get_workspace_spectator(session, workspace_id=workspace.id)

    return SpectatorAccessState(
        workspace_slug=workspace.slug,
        configured=spectator is not None,
        enabled=spectator is not None and spectator.is_active,
    )


def create_workspace_spectator(
    session: Session,
    *,
    workspace_id: int,
    password: str,
) -> User:
    """Create the workspace's one spectator. The caller holds the workspace lock."""

    validate_spectator_password(password)

    if get_workspace_spectator(session, workspace_id=workspace_id, for_update=True) is not None:
        raise SpectatorAlreadyExistsError("Shared spectator access is already configured.")

    try:
        with session.begin_nested():
            spectator = AuthenticationRepository(session).create_user(
                username=generate_internal_spectator_username(),
                display_name=SPECTATOR_DISPLAY_NAME,
                email=None,
                password=password,
                role=UserRole.SPECTATOR,
                workspace_id=workspace_id,
                is_active=True,
            )
    except IntegrityError as exc:
        if _constraint_name(exc) == ONE_SPECTATOR_CONSTRAINT:
            raise SpectatorAlreadyExistsError(
                "Shared spectator access is already configured."
            ) from exc

        raise

    return spectator


def reset_workspace_spectator_password(
    session: Session,
    *,
    workspace_id: int,
    password: str,
    now: datetime | None = None,
) -> tuple[User, int]:
    """Replace the password and revoke every spectator session."""

    validate_spectator_password(password)
    lock_workspace(session, workspace_id)
    spectator = _require_spectator(session, workspace_id)
    spectator.password_hash = hash_password(password)
    revoked = SessionRepository(session).revoke_all_user_sessions(
        user_id=spectator.id,
        reason=SessionRevocationReason.SPECTATOR_PASSWORD_RESET,
        now=now or datetime.now(UTC),
    )
    session.flush()

    return spectator, revoked


def disable_workspace_spectator(
    session: Session,
    *,
    workspace_id: int,
    now: datetime | None = None,
) -> tuple[User, int]:
    """Disable access and revoke every spectator session."""

    lock_workspace(session, workspace_id)
    spectator = _require_spectator(session, workspace_id)
    spectator.is_active = False
    revoked = SessionRepository(session).revoke_all_user_sessions(
        user_id=spectator.id,
        reason=SessionRevocationReason.SPECTATOR_DISABLED,
        now=now or datetime.now(UTC),
    )
    session.flush()

    return spectator, revoked


def enable_workspace_spectator(
    session: Session,
    *,
    workspace_id: int,
    password: str,
    now: datetime | None = None,
) -> tuple[User, int]:
    """Re-enable access with a new password; the old one is never revealed or reused.

    Any remaining spectator session is revoked defensively. The reason is
    spectator_password_reset because the shared password is replaced.
    """

    validate_spectator_password(password)
    lock_workspace(session, workspace_id)
    spectator = _require_spectator(session, workspace_id)
    spectator.password_hash = hash_password(password)
    spectator.is_active = True
    revoked = SessionRepository(session).revoke_all_user_sessions(
        user_id=spectator.id,
        reason=SessionRevocationReason.SPECTATOR_PASSWORD_RESET,
        now=now or datetime.now(UTC),
    )
    session.flush()

    return spectator, revoked


def _require_spectator(session: Session, workspace_id: int) -> User:
    spectator = get_workspace_spectator(session, workspace_id=workspace_id, for_update=True)

    if spectator is None:
        raise SpectatorNotFoundError("Shared spectator access is not configured.")

    return spectator


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)

    return getattr(diagnostic, "constraint_name", None)
