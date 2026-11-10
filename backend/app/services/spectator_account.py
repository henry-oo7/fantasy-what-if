"""Manage the installation's single shared spectator account."""

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.db import model_registry  # noqa: F401
from app.db.auth_models import User, UserRole
from app.repositories.auth_repository import (
    AuthenticationRepository,
    normalize_display_name,
    normalize_email,
)
from app.repositories.session_repository import (
    SessionRepository,
)

SPECTATOR_ACCOUNT_LOCK_KEY = 1_179_921_931


class SpectatorAccountError(RuntimeError):
    """Base error for shared spectator management."""


class SpectatorAlreadyExistsError(SpectatorAccountError):
    """The installation already has a spectator."""


class SpectatorNotFoundError(SpectatorAccountError):
    """The installation has no spectator account."""


class SpectatorAccountStateError(SpectatorAccountError):
    """The stored spectator account state is invalid."""


def get_spectator_account(
    session: Session,
    *,
    for_update: bool = False,
) -> User | None:
    """Return the single spectator account, if configured."""

    statement = select(User).where(User.role == UserRole.SPECTATOR).order_by(User.id)

    if for_update:
        statement = statement.with_for_update()

    spectators = session.scalars(statement).all()

    if len(spectators) > 1:
        raise SpectatorAccountStateError("Multiple spectator accounts are configured.")

    return spectators[0] if spectators else None


def create_spectator_account(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    email: str | None = None,
) -> User:
    """Create the one shared spectator account."""

    _acquire_spectator_lock(session)

    if (
        get_spectator_account(
            session,
            for_update=True,
        )
        is not None
    ):
        raise SpectatorAlreadyExistsError("A spectator account is already configured.")

    return AuthenticationRepository(session).create_user(
        username=username,
        display_name=display_name,
        email=email,
        password=password,
        role=UserRole.SPECTATOR,
        is_active=True,
    )


def update_spectator_profile(
    session: Session,
    *,
    display_name: str,
    email: str | None,
) -> User:
    """Update safe profile fields for the spectator."""

    _acquire_spectator_lock(session)

    spectator = _require_spectator(
        session,
        for_update=True,
    )

    normalized_display_name = normalize_display_name(display_name)

    normalized_email = normalize_email(email) if email is not None and email.strip() else None

    if normalized_email is not None:
        existing = AuthenticationRepository(session).get_user_by_email(normalized_email)

        if existing is not None and existing.id != spectator.id:
            from app.repositories.auth_repository import (
                DuplicateEmailError,
            )

            raise DuplicateEmailError("That email address is already registered.")

    spectator.display_name = normalized_display_name
    spectator.email = normalized_email

    session.flush()

    return spectator


def reset_spectator_password(
    session: Session,
    *,
    password: str,
    now: datetime | None = None,
) -> tuple[User, int]:
    """Replace the password and revoke spectator sessions."""

    _acquire_spectator_lock(session)

    spectator = _require_spectator(
        session,
        for_update=True,
    )

    spectator.password_hash = hash_password(password)

    revoked_count = SessionRepository(session).revoke_all_user_sessions(
        user_id=spectator.id,
        now=now or datetime.now(UTC),
    )

    session.flush()

    return spectator, revoked_count


def set_spectator_active(
    session: Session,
    *,
    is_active: bool,
    now: datetime | None = None,
) -> tuple[User, int]:
    """Enable or disable spectator access."""

    _acquire_spectator_lock(session)

    spectator = _require_spectator(
        session,
        for_update=True,
    )

    spectator.is_active = is_active

    revoked_count = 0

    if not is_active:
        revoked_count = SessionRepository(session).revoke_all_user_sessions(
            user_id=spectator.id,
            now=now or datetime.now(UTC),
        )

    session.flush()

    return spectator, revoked_count


def _require_spectator(
    session: Session,
    *,
    for_update: bool,
) -> User:
    spectator = get_spectator_account(
        session,
        for_update=for_update,
    )

    if spectator is None:
        raise SpectatorNotFoundError("A spectator account is not configured.")

    return spectator


def _acquire_spectator_lock(
    session: Session,
) -> None:
    session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                :lock_key
            )
            """
        ),
        {
            "lock_key": SPECTATOR_ACCOUNT_LOCK_KEY,
        },
    )
