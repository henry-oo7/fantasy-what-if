"""One-time installation administrator bootstrap."""

from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.auth_models import User, UserRole
from app.db.platform_enums import InstallationStatus
from app.db.platform_models import Installation
from app.repositories.auth_repository import (
    AuthenticationRepository,
)

SETUP_BOOTSTRAP_LOCK_KEY = 1_179_921_928


class SetupBootstrapUnavailableError(RuntimeError):
    """The one-time administrator bootstrap is unavailable."""


class SetupBootstrapStateError(RuntimeError):
    """The installation lifecycle is structurally invalid."""


def bootstrap_administrator(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    email: str | None = None,
    now: datetime | None = None,
) -> tuple[Installation, User]:
    """Create the first administrator and begin setup atomically."""

    session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                :lock_key
            )
            """
        ),
        {
            "lock_key": SETUP_BOOTSTRAP_LOCK_KEY,
        },
    )

    installations = session.scalars(
        select(Installation).order_by(Installation.id).with_for_update()
    ).all()

    if len(installations) > 1:
        raise SetupBootstrapStateError("Multiple installations are configured.")

    user_count = int(session.scalar(select(func.count()).select_from(User)) or 0)

    if user_count != 0:
        raise SetupBootstrapUnavailableError("Administrator bootstrap is no longer available.")

    timestamp = now or datetime.now(UTC)

    if installations:
        installation = installations[0]

        if installation.status is InstallationStatus.ACTIVE:
            raise SetupBootstrapUnavailableError("Administrator bootstrap is no longer available.")
    else:
        installation = Installation(
            application_name="Fantasy What-If",
            timezone="America/Chicago",
            status=InstallationStatus.SETUP_REQUIRED,
        )
        session.add(installation)
        session.flush()

    installation.status = InstallationStatus.SETUP_IN_PROGRESS

    if installation.setup_started_at is None:
        installation.setup_started_at = timestamp

    administrator = AuthenticationRepository(session).create_user(
        username=username,
        display_name=display_name,
        email=email,
        password=password,
        role=UserRole.ADMIN,
        is_active=True,
    )

    return installation, administrator
