"""Persistence primitives for per-user League Season access (ADR 0010).

Personal League and Season reads authorize through these rows (see
``app.services.league_season_authorization``). Every method takes explicit
user and/or Season identifiers; none selects a "first" or "only" row.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    SeasonStatus,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import ProviderConnection
from app.repositories.provider_league_claim_repository import (
    claimed_workspace_id,
    normalize_provider_league_id,
)


class LeagueAccessRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create_preferences(self, *, user_id: int) -> UserPreference:
        self.session.execute(
            insert(UserPreference).values(user_id=user_id).on_conflict_do_nothing()
        )
        return self.session.execute(
            select(UserPreference)
            .where(UserPreference.user_id == user_id)
            .execution_options(populate_existing=True)
        ).scalar_one()

    def get_league_by_identity(
        self, *, provider: FantasyProvider, provider_league_id: str
    ) -> League | None:
        return self.session.scalar(
            select(League).where(
                League.provider == provider,
                League.provider_league_id == normalize_provider_league_id(provider_league_id),
            )
        )

    def lock_or_create_league(
        self, *, provider: FantasyProvider, provider_league_id: str, name: str
    ) -> League:
        """Return the locked canonical League, inserting it only if the identity is new.

        The unique identity index decides the race; ``ON CONFLICT DO NOTHING``
        targets only that index, so any other integrity failure still raises.
        Never merges, deletes or reassigns an existing League. If an orphan
        purge deletes the row while this waits for its lock, the identity is
        inserted again once.
        """

        normalized = normalize_provider_league_id(provider_league_id)

        for _ in range(2):
            self.session.execute(
                insert(League)
                .values(provider=provider, provider_league_id=normalized, name=name)
                .on_conflict_do_nothing(
                    index_elements=[League.provider, League.provider_league_id],
                    index_where=text("provider IS NOT NULL AND provider_league_id IS NOT NULL"),
                )
            )
            league = self.session.execute(
                select(League)
                .where(League.provider == provider, League.provider_league_id == normalized)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()

            if league is not None:
                return league

        raise RuntimeError("The canonical League was deleted repeatedly while being locked.")

    def create_or_get_season(
        self, *, league_id: int, year: int, current_week: int, regular_season_weeks: int
    ) -> Season:
        """Return the League's Season for the year, inserting it only if absent.

        ``ON CONFLICT`` targets only ``uq_seasons_league_year``; any other
        integrity failure still raises. An existing Season is never modified:
        synchronization owns its metadata.
        """

        self.session.execute(
            insert(Season)
            .values(
                league_id=league_id,
                year=year,
                current_week=current_week,
                regular_season_weeks=regular_season_weeks,
                provider_season_id=str(year),
                status=SeasonStatus.DRAFT,
                sync_state=SeasonSyncState.NEVER_SYNCED,
            )
            .on_conflict_do_nothing(constraint="uq_seasons_league_year")
        )

        return self.session.execute(
            select(Season)
            .where(Season.league_id == league_id, Season.year == year)
            .execution_options(populate_existing=True)
        ).scalar_one()

    def create_or_get_access(
        self,
        *,
        user_id: int,
        season_id: int,
        proof_mode: UserSeasonProofMode,
        provider_connection_id: int | None,
        verified_at: datetime,
    ) -> UserLeagueSeasonAccess:
        """Insert one active access row, or return the user's existing row unchanged.

        Reactivating or re-proving an existing row belongs to the import
        service. The League row is locked so an orphan mark cannot interleave;
        a new active row clears ``orphaned_at`` under that lock.
        """

        league = self.session.execute(
            select(League)
            .join(Season, Season.league_id == League.id)
            .where(Season.id == season_id)
            .with_for_update(of=League)
        ).scalar_one()

        inserted = self.session.execute(
            insert(UserLeagueSeasonAccess)
            .values(
                user_id=user_id,
                season_id=season_id,
                provider_connection_id=provider_connection_id,
                proof_mode=proof_mode,
                status=UserSeasonAccessStatus.ACTIVE,
                verified_at=verified_at,
            )
            .on_conflict_do_nothing(constraint="uq_user_league_season_access_user_season")
            .returning(UserLeagueSeasonAccess.id)
        ).scalar_one_or_none()

        if inserted is not None:
            league.orphaned_at = None

        return self.session.execute(
            select(UserLeagueSeasonAccess)
            .where(
                UserLeagueSeasonAccess.user_id == user_id,
                UserLeagueSeasonAccess.season_id == season_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one()

    def list_active_access_for_user(self, *, user_id: int) -> list[UserLeagueSeasonAccess]:
        return list(
            self.session.scalars(
                select(UserLeagueSeasonAccess)
                .where(
                    UserLeagueSeasonAccess.user_id == user_id,
                    UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
                )
                .order_by(UserLeagueSeasonAccess.id)
            )
        )

    def lock_user_season_access(
        self,
        *,
        user_id: int,
        season_id: int,
        statuses: Collection[UserSeasonAccessStatus],
    ) -> tuple[UserLeagueSeasonAccess, Season, League] | None:
        """This user's access row for the Season, share-locked until the transaction ends.

        The share lock makes a concurrent revocation or deletion of the row wait
        for this transaction, and a read that waited on one re-checks the
        committed status. Only the access row is locked.
        """

        row = self.session.execute(
            select(UserLeagueSeasonAccess, Season, League)
            .join(Season, Season.id == UserLeagueSeasonAccess.season_id)
            .join(League, League.id == Season.league_id)
            .where(
                UserLeagueSeasonAccess.user_id == user_id,
                UserLeagueSeasonAccess.season_id == season_id,
                UserLeagueSeasonAccess.status.in_(statuses),
            )
            .with_for_update(read=True, of=UserLeagueSeasonAccess)
        ).one_or_none()

        return None if row is None else tuple(row)

    def list_user_season_access(
        self,
        *,
        user_id: int,
        statuses: Collection[UserSeasonAccessStatus],
    ) -> list[tuple[UserLeagueSeasonAccess, Season, League]]:
        """This user's access rows with their Season and League, grouped by League."""

        return [
            tuple(row)
            for row in self.session.execute(
                select(UserLeagueSeasonAccess, Season, League)
                .join(Season, Season.id == UserLeagueSeasonAccess.season_id)
                .join(League, League.id == Season.league_id)
                .where(
                    UserLeagueSeasonAccess.user_id == user_id,
                    UserLeagueSeasonAccess.status.in_(statuses),
                )
                .order_by(League.name, League.id, Season.year.desc(), Season.id)
            )
        ]

    def get_default_season_id(self, *, user_id: int) -> int | None:
        return self.session.scalar(
            select(UserPreference.default_league_season_id).where(UserPreference.user_id == user_id)
        )

    def list_eligible_access_for_season(self, *, season_id: int) -> list[UserLeagueSeasonAccess]:
        return list(
            self.session.scalars(
                select(UserLeagueSeasonAccess)
                .where(
                    UserLeagueSeasonAccess.season_id == season_id,
                    UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
                )
                .order_by(UserLeagueSeasonAccess.id)
            )
        )

    def mark_league_orphaned(self, *, league_id: int, orphaned_at: datetime) -> bool:
        """Set ``orphaned_at`` only while the League has no active access and no
        temporary workspace dependency (a matching claim). Keeps an earlier mark."""

        league = self.session.scalar(select(League).where(League.id == league_id).with_for_update())
        if league is None or claimed_workspace_id(self.session, league) is not None:
            return False

        active = self.session.scalar(
            select(func.count())
            .select_from(UserLeagueSeasonAccess)
            .join(Season, Season.id == UserLeagueSeasonAccess.season_id)
            .where(
                Season.league_id == league_id,
                UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
            )
        )
        if active:
            return False

        if league.orphaned_at is None:
            league.orphaned_at = orphaned_at
        return True

    def clear_league_orphaned(self, *, league_id: int) -> None:
        self.session.execute(update(League).where(League.id == league_id).values(orphaned_at=None))

    def count_connection_access_usage(self, *, user_id: int, provider_connection_id: int) -> int:
        """Access rows backed by a connection, only if that user owns the connection."""

        return (
            self.session.scalar(
                select(func.count())
                .select_from(UserLeagueSeasonAccess)
                .join(
                    ProviderConnection,
                    ProviderConnection.id == UserLeagueSeasonAccess.provider_connection_id,
                )
                .where(
                    ProviderConnection.id == provider_connection_id,
                    ProviderConnection.user_id == user_id,
                )
            )
            or 0
        )
