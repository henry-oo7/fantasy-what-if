"""Persistence primitives for per-user League Season access (ADR 0010).

Schema-proving only: no route or service uses these as authorization yet.
Every method takes explicit user and/or Season identifiers; none selects a
"first" or "only" row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import ProviderConnection
from app.repositories.provider_league_claim_repository import normalize_provider_league_id


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
        Never merges, deletes or reassigns an existing League.
        """

        normalized = normalize_provider_league_id(provider_league_id)
        self.session.execute(
            insert(League)
            .values(provider=provider, provider_league_id=normalized, name=name)
            .on_conflict_do_nothing(
                index_elements=[League.provider, League.provider_league_id],
                index_where=text("provider IS NOT NULL AND provider_league_id IS NOT NULL"),
            )
        )

        return self.session.execute(
            select(League)
            .where(League.provider == provider, League.provider_league_id == normalized)
            .with_for_update()
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
        """Set ``orphaned_at`` only while the League has no active access."""

        league = self.session.scalar(select(League).where(League.id == league_id).with_for_update())
        if league is None:
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
