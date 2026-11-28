"""Synthetic fixtures for personal Season maintenance tests.

Committed rows through ``SessionFactory`` so race tests can use independent
PostgreSQL connections. Numeric synthetic ESPN IDs and credentials only; every
provider response comes from ``FakeEspn``. All rows are tracked and removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update

from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
    ProviderAccessMode,
    SeasonStatus,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import BackgroundJob, ProviderConnection
from app.db.session import SessionFactory
from tests.support.league_import import unique_league_id
from tests.support.provider_sync_fixtures import encrypt_synthetic_credentials

SEASON_YEAR = datetime.now(UTC).year


@dataclass
class Member:
    user_id: int
    connection_id: int | None
    swid: str
    espn_s2: str


@dataclass
class Target:
    league_id: int
    season_id: int
    provider_league_id: str


@dataclass
class World:
    leagues: list[int] = field(default_factory=list)
    users: list[int] = field(default_factory=list)

    def season(self, **values: Any) -> Target:
        provider_league_id = unique_league_id()
        year = values.pop("year", SEASON_YEAR)

        with SessionFactory() as session:
            league = League(
                name="Maintenance Synthetic League",
                provider=FantasyProvider.ESPN,
                provider_league_id=provider_league_id,
                sport=FantasySport.FOOTBALL,
                orphaned_at=values.pop("orphaned_at", None),
            )
            session.add(league)
            session.flush()
            season = Season(
                league_id=league.id,
                year=year,
                current_week=1,
                regular_season_weeks=3,
                provider_season_id=str(year),
                status=SeasonStatus.DRAFT,
                sync_state=values.pop("sync_state", SeasonSyncState.FRESH),
                **values,
            )
            session.add(season)
            session.commit()
            self.leagues.append(league.id)

            return Target(league.id, season.id, provider_league_id)

    def member(
        self,
        target: Target,
        *,
        proof: UserSeasonProofMode = UserSeasonProofMode.PRIVATE_CREDENTIALS,
        status: UserSeasonAccessStatus = UserSeasonAccessStatus.ACTIVE,
        verified_ago: timedelta = timedelta(days=8),
        user_active: bool = True,
        with_connection: bool | None = None,
        connection_error: str | None = None,
        year: int = SEASON_YEAR,
    ) -> Member:
        """Access due for reverification by default (verified eight days ago)."""

        suffix = uuid4().hex[:10]
        swid = f"{{MAINT-SWID-{suffix}}}"
        espn_s2 = f"MAINT-S2-{suffix}"
        private = proof is UserSeasonProofMode.PRIVATE_CREDENTIALS
        connection_wanted = private if with_connection is None else with_connection

        with SessionFactory() as session:
            user = User(
                username=f"maint-{suffix}",
                display_name=f"Maintenance {suffix}",
                email=f"maint-{suffix}@example.invalid",
                normalized_email=f"maint-{suffix}@example.invalid",
                password_hash="$argon2id$synthetic",
                role=UserRole.SPECTATOR,
                is_active=user_active,
            )
            session.add(user)
            session.flush()
            self.users.append(user.id)
            connection_id = None

            if connection_wanted:
                connection = ProviderConnection(
                    user_id=user.id,
                    provider=FantasyProvider.ESPN,
                    display_name=f"Maintenance {suffix}",
                    encrypted_credentials=encrypt_synthetic_credentials(
                        target.provider_league_id, swid=swid, espn_s2=espn_s2
                    ),
                    credential_version=1,
                    is_active=True,
                    provider_league_id=target.provider_league_id,
                    provider_season_year=year,
                    access_mode=ProviderAccessMode.PRIVATE,
                    last_validated_at=datetime.now(UTC) - verified_ago,
                    last_validation_error=connection_error,
                )
                session.add(connection)
                session.flush()
                connection_id = connection.id

            session.add(
                UserLeagueSeasonAccess(
                    user_id=user.id,
                    season_id=target.season_id,
                    provider_connection_id=connection_id if private else None,
                    proof_mode=proof,
                    status=status,
                    verified_at=datetime.now(UTC) - verified_ago,
                )
            )
            session.commit()

        return Member(user.id, connection_id, swid, espn_s2)

    def cleanup(self) -> None:
        with SessionFactory() as session:
            season_ids = list(
                session.scalars(select(Season.id).where(Season.league_id.in_(self.leagues)))
            )
            keys = [f"league-season-sync:{season_id}" for season_id in season_ids] + [
                f"access-reverification:{user_id}:{season_id}"
                for user_id in self.users
                for season_id in season_ids
            ]
            session.execute(delete(BackgroundJob).where(BackgroundJob.idempotency_key.in_(keys)))
            session.execute(delete(League).where(League.id.in_(self.leagues)))
            session.execute(
                delete(ProviderConnection).where(ProviderConnection.user_id.in_(self.users))
            )
            session.execute(delete(User).where(User.id.in_(self.users)))
            session.commit()


def access_row(member: Member, season_id: int) -> UserLeagueSeasonAccess | None:
    with SessionFactory() as session:
        row = session.scalar(
            select(UserLeagueSeasonAccess).where(
                UserLeagueSeasonAccess.user_id == member.user_id,
                UserLeagueSeasonAccess.season_id == season_id,
            )
        )
        if row is not None:
            session.expunge(row)
        return row


def connection_row(connection_id: int) -> ProviderConnection | None:
    with SessionFactory() as session:
        row = session.get(ProviderConnection, connection_id)
        if row is not None:
            session.expunge(row)
        return row


def season_row(season_id: int) -> Season:
    with SessionFactory() as session:
        season = session.get(Season, season_id)
        assert season is not None
        session.expunge(season)
        return season


def update_season(season_id: int, **values: Any) -> None:
    with SessionFactory() as session:
        session.execute(update(Season).where(Season.id == season_id).values(**values))
        session.commit()


def update_access(member: Member, season_id: int, **values: Any) -> None:
    with SessionFactory() as session:
        session.execute(
            update(UserLeagueSeasonAccess)
            .where(
                UserLeagueSeasonAccess.user_id == member.user_id,
                UserLeagueSeasonAccess.season_id == season_id,
            )
            .values(**values)
        )
        session.commit()


def jobs_for(*keys: str) -> list[BackgroundJob]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(select(BackgroundJob).where(BackgroundJob.idempotency_key.in_(keys)))
        )
        session.expunge_all()
        return rows


def scope_discovery(monkeypatch, world: World) -> None:
    """Restrict maintenance discovery to this test's rows so no stray job is committed."""

    from app.services import season_maintenance  # noqa: PLC0415

    sync, reverification = (
        season_maintenance.list_due_season_syncs,
        season_maintenance.list_due_reverifications,
    )

    def own_seasons(session, **kwargs):
        owned = set(session.scalars(select(Season.id).where(Season.league_id.in_(world.leagues))))
        return [season_id for season_id in sync(session, **kwargs) if season_id in owned]

    def own_users(session, **kwargs):
        return [pair for pair in reverification(session, **kwargs) if pair[0] in world.users]

    monkeypatch.setattr(season_maintenance, "list_due_season_syncs", own_seasons)
    monkeypatch.setattr(season_maintenance, "list_due_reverifications", own_users)
