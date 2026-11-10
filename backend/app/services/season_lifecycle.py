"""Provider-season lifecycle classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.db.platform_enums import SeasonStatus


class ImportedSeasonKind(StrEnum):
    """Relationship between an imported season and the current year."""

    HISTORICAL = "historical"
    CURRENT = "current"


class FutureSeasonImportError(ValueError):
    """The requested provider season has not begun yet."""


@dataclass(frozen=True, slots=True)
class ImportedSeasonLifecycle:
    """Lifecycle state assigned after a successful full import."""

    kind: ImportedSeasonKind
    status: SeasonStatus
    synchronization_enabled: bool
    activated_at: datetime | None
    completed_at: datetime | None


def classify_imported_season(
    *,
    season_year: int,
    now: datetime | None = None,
) -> ImportedSeasonLifecycle:
    """Classify an imported season against the server's current year."""

    timestamp = now or datetime.now(UTC)
    current_year = timestamp.year

    if season_year < 2000:
        raise ValueError("Provider season year must be 2000 or later.")

    if season_year > current_year:
        raise FutureSeasonImportError(
            f"Season {season_year} cannot be imported before the {season_year} calendar year."
        )

    if season_year < current_year:
        return ImportedSeasonLifecycle(
            kind=ImportedSeasonKind.HISTORICAL,
            status=SeasonStatus.COMPLETED,
            synchronization_enabled=False,
            activated_at=None,
            completed_at=timestamp,
        )

    return ImportedSeasonLifecycle(
        kind=ImportedSeasonKind.CURRENT,
        status=SeasonStatus.ACTIVE,
        synchronization_enabled=True,
        activated_at=timestamp,
        completed_at=None,
    )
