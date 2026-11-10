"""Reconcile synchronization lifecycle state across calendar years."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import Season
from app.db.platform_enums import (
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import Installation


class SeasonLifecycleReconciliationError(RuntimeError):
    """Stored installation lifecycle state cannot be reconciled safely."""


@dataclass(frozen=True, slots=True)
class SeasonLifecycleReconciliationResult:
    """Summary of one lifecycle reconciliation attempt."""

    changed: bool
    installation_id: int | None
    completed_season_id: int | None
    completed_season_year: int | None


def reconcile_stale_active_season(
    session: Session,
    *,
    now: datetime | None = None,
    commit_transaction: bool = True,
) -> SeasonLifecycleReconciliationResult:
    """Complete a prior-year synchronization season.

    A season may synchronize only during its matching calendar year. When the
    server reaches a later calendar year, the stale active season becomes
    completed and the installation no longer has a synchronization target.
    The active league reference remains intact for historical viewing.
    """

    timestamp = now or datetime.now(UTC)

    installations = session.scalars(
        select(Installation)
        .where(
            Installation.status == InstallationStatus.ACTIVE,
        )
        .order_by(Installation.id)
        .with_for_update()
    ).all()

    if not installations:
        return SeasonLifecycleReconciliationResult(
            changed=False,
            installation_id=None,
            completed_season_id=None,
            completed_season_year=None,
        )

    if len(installations) > 1:
        raise SeasonLifecycleReconciliationError(
            "Multiple active installations cannot be reconciled safely."
        )

    installation = installations[0]

    if installation.active_season_id is None:
        return SeasonLifecycleReconciliationResult(
            changed=False,
            installation_id=installation.id,
            completed_season_id=None,
            completed_season_year=None,
        )

    season = session.get(
        Season,
        installation.active_season_id,
        with_for_update=True,
    )

    if season is None:
        raise SeasonLifecycleReconciliationError(
            "The active installation references a missing season."
        )

    if season.year >= timestamp.year:
        return SeasonLifecycleReconciliationResult(
            changed=False,
            installation_id=installation.id,
            completed_season_id=None,
            completed_season_year=None,
        )

    season.status = SeasonStatus.COMPLETED
    season.activated_at = None
    season.completed_at = season.completed_at or timestamp
    installation.active_season_id = None

    session.flush()

    if commit_transaction:
        session.commit()

    return SeasonLifecycleReconciliationResult(
        changed=True,
        installation_id=installation.id,
        completed_season_id=season.id,
        completed_season_year=season.year,
    )
