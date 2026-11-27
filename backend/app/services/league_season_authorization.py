"""Per-user League Season authorization for personal reads (ADR 0010).

The ``user_league_season_access`` row is the only authority. Workspace,
role, ``League.installation_id``, ``ProviderLeagueClaim``, provider
connections, the default-Season preference and the existence of canonical
data never grant access.

Readable access:

- ``active`` reads.
- ``reverification_required`` keeps reading. ADR 0010 sets that state for
  ambiguous provider outcomes precisely so they do not revoke; responses
  carry the status as the bounded indicator.
- ``revoked``, a missing row and a missing Season are indistinguishable
  (``LeagueSeasonNotFoundError``).

Season data is served only once a successful synchronization is recorded
(``has_successful_data``); Season ``status`` (workspace draft/active) is
never consulted.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import UserSeasonAccessStatus
from app.repositories.league_access_repository import LeagueAccessRepository
from app.services.season_sync_state import has_successful_data

READABLE_ACCESS_STATUSES = frozenset(
    {UserSeasonAccessStatus.ACTIVE, UserSeasonAccessStatus.REVERIFICATION_REQUIRED}
)


class LeagueSeasonNotFoundError(LookupError):
    """Uniform outcome for a missing, inaccessible or revoked League Season."""


class LeagueSeasonDataUnavailableError(RuntimeError):
    """The authorized Season has no successfully synchronized data yet."""


@dataclass(frozen=True, slots=True)
class AuthorizedLeagueSeason:
    """One Season the user may read, in the transaction that authorized it."""

    access: UserLeagueSeasonAccess
    season: Season
    league: League

    @property
    def data_available(self) -> bool:
        return has_successful_data(self.season)

    def require_data(self) -> None:
        if not self.data_available:
            raise LeagueSeasonDataUnavailableError


def require_league_season_access(
    session: Session, *, user_id: int, season_id: int
) -> AuthorizedLeagueSeason:
    """Authorize one Season for this user, or raise the uniform not-found error.

    The access row stays share-locked until the caller's transaction ends, so
    everything read in that transaction is read under authorization that a
    concurrent revocation cannot overtake.
    """

    row = LeagueAccessRepository(session).lock_user_season_access(
        user_id=user_id, season_id=season_id, statuses=READABLE_ACCESS_STATUSES
    )

    if row is None:
        raise LeagueSeasonNotFoundError

    return AuthorizedLeagueSeason(*row)


def list_readable_league_seasons(session: Session, *, user_id: int) -> list[AuthorizedLeagueSeason]:
    """Every Season this user may read, grouped by League (name, then ID)."""

    return [
        AuthorizedLeagueSeason(*row)
        for row in LeagueAccessRepository(session).list_user_season_access(
            user_id=user_id, statuses=READABLE_ACCESS_STATUSES
        )
    ]


def readable_default_season_id(
    session: Session, *, user_id: int, readable: list[AuthorizedLeagueSeason]
) -> int | None:
    """The stored default only while it is ``readable``; a stale one is ignored, not cleared."""

    default = LeagueAccessRepository(session).get_default_season_id(user_id=user_id)
    return default if any(item.season.id == default for item in readable) else None


def set_default_season(session: Session, *, user_id: int, season_id: int) -> None:
    """Make a Season this user may read their default; the caller commits."""

    require_league_season_access(session, user_id=user_id, season_id=season_id)
    LeagueAccessRepository(session).get_or_create_preferences(
        user_id=user_id
    ).default_league_season_id = season_id
