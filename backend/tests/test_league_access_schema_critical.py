"""Critical invariants of the canonical League and per-user access schema.

Committed data against the guarded test database, removed by tag afterwards.
Concurrency tests use real PostgreSQL row/unique-index locks and observe
lock waits instead of sleeping, so ordering is deterministic.
"""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    UserSeasonProofMode,
)
from app.db.platform_models import Installation, ProviderConnection
from app.db.session import engine
from app.repositories.league_access_repository import LeagueAccessRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Seeder:
    def __init__(self) -> None:
        self.tag = uuid4().hex[:10]
        self.user_ids: list[int] = []
        self.league_ids: list[int] = []
        self.installation_ids: list[int] = []
        self.counter = 0

    def next_id(self) -> str:
        self.counter += 1
        return f"{self.tag}{self.counter}"

    def user(self, session: Session) -> User:
        user = User(
            username=f"u-{self.next_id()}",
            display_name="Synthetic User",
            password_hash="$argon2id$synthetic",
            role=UserRole.SPECTATOR,
        )
        session.add(user)
        session.flush()
        self.user_ids.append(user.id)
        return user

    def league(self, session: Session, *, league_id: str | None = None) -> League:
        league = League(
            name="Synthetic League",
            provider=FantasyProvider.ESPN,
            provider_league_id=league_id or f"9{int(self.tag, 16)}{self.counter}",
        )
        self.counter += 1
        session.add(league)
        session.flush()
        self.league_ids.append(league.id)
        return league

    def season(self, session: Session, league: League, year: int = 2025) -> Season:
        season = Season(league_id=league.id, year=year, current_week=1, regular_season_weeks=14)
        session.add(season)
        session.flush()
        return season

    def connection(self, session: Session, user: User) -> ProviderConnection:
        connection = ProviderConnection(
            user_id=user.id,
            provider=FantasyProvider.ESPN,
            display_name="Synthetic Connection",
        )
        session.add(connection)
        session.flush()
        return connection

    def cleanup(self) -> None:
        with Session(engine) as session:
            session.execute(
                delete(UserLeagueSeasonAccess).where(
                    UserLeagueSeasonAccess.user_id.in_(self.user_ids)
                )
            )
            session.execute(delete(League).where(League.id.in_(self.league_ids)))
            session.execute(delete(User).where(User.id.in_(self.user_ids)))
            session.execute(delete(Installation).where(Installation.id.in_(self.installation_ids)))
            session.commit()


@pytest.fixture
def seeder() -> Iterator[Seeder]:
    seeder = Seeder()
    try:
        yield seeder
    finally:
        seeder.cleanup()


@pytest.fixture
def committed(seeder: Seeder) -> Iterator[tuple[Seeder, Session]]:
    with Session(engine) as session:
        yield seeder, session


def make_world(seeder: Seeder, session: Session):
    user_a, user_b = seeder.user(session), seeder.user(session)
    league = seeder.league(session)
    season = seeder.season(session, league)
    session.commit()
    return user_a, user_b, league, season


def wait_for_lock_wait(expected: int = 1, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                    "AND pid <> pg_backend_pid()"
                )
            )
        if waiting >= expected:
            return
        time.sleep(0.02)
    raise AssertionError("expected session never blocked on a lock")


def run_in_thread(target) -> tuple[threading.Thread, dict]:
    result: dict = {}

    def runner() -> None:
        try:
            result["value"] = target()
        except Exception as exc:  # noqa: BLE001 - surfaced to the asserting test
            result["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    return thread, result


def test_canonical_identity_is_unique_per_provider(committed) -> None:
    seeder, session = committed
    league = seeder.league(session)
    session.commit()

    session.add(
        League(
            name="Duplicate",
            provider=FantasyProvider.ESPN,
            provider_league_id=league.provider_league_id,
        )
    )
    with pytest.raises(IntegrityError, match="uq_leagues_provider_identity"):
        session.flush()
    session.rollback()

    other = League(
        name="Other provider",
        provider=FantasyProvider.SLEEPER,
        provider_league_id=league.provider_league_id,
    )
    legacy_a, legacy_b = (League(name="Legacy A"), League(name="Legacy B"))
    session.add_all([other, legacy_a, legacy_b])
    session.commit()  # same id on another provider and NULL-identity legacy rows stay valid
    seeder.league_ids.extend([other.id, legacy_a.id, legacy_b.id])


def test_concurrent_league_insert_leaves_exactly_one_row(seeder: Seeder) -> None:
    identity = f"7{int(seeder.tag, 16)}"
    first = Session(engine)
    first.add(League(name="First", provider=FantasyProvider.ESPN, provider_league_id=identity))
    first.flush()

    def second_insert() -> int:
        with Session(engine) as session:
            league = League(
                name="Second", provider=FantasyProvider.ESPN, provider_league_id=identity
            )
            session.add(league)
            session.commit()
            return league.id

    thread, result = run_in_thread(second_insert)
    wait_for_lock_wait()  # second insert is blocked on the first's unique entry
    first.commit()
    seeder.league_ids.extend(first.scalars(select(League.id).where(League.name == "First")))
    first.close()
    thread.join(timeout=10)

    assert isinstance(result.get("error"), IntegrityError)
    with Session(engine) as session:
        count = session.scalar(
            select(func.count()).select_from(League).where(League.provider_league_id == identity)
        )
    assert count == 1


def test_two_users_share_one_season_with_independent_access(committed) -> None:
    seeder, session = committed
    user_a, user_b, league, season = make_world(seeder, session)
    repository = LeagueAccessRepository(session)

    access_a = repository.create_or_get_access(
        user_id=user_a.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PUBLIC,
        provider_connection_id=None,
        verified_at=NOW,
    )
    session.commit()

    assert repository.list_active_access_for_user(user_id=user_b.id) == []
    assert [a.user_id for a in repository.list_eligible_access_for_season(season_id=season.id)] == [
        user_a.id
    ]

    repository.create_or_get_access(
        user_id=user_b.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PUBLIC,
        provider_connection_id=None,
        verified_at=NOW,
    )
    session.commit()
    assert {a.user_id for a in repository.list_eligible_access_for_season(season_id=season.id)} == {
        user_a.id,
        user_b.id,
    }

    session.delete(access_a)
    session.commit()

    assert [a.user_id for a in repository.list_eligible_access_for_season(season_id=season.id)] == [
        user_b.id
    ]
    assert session.get(League, league.id) is not None
    assert session.get(Season, season.id) is not None


def test_user_deletion_removes_personal_rows_not_shared_data(committed) -> None:
    seeder, session = committed
    user_a, user_b, league, season = make_world(seeder, session)
    repository = LeagueAccessRepository(session)
    for user in (user_a, user_b):
        repository.get_or_create_preferences(user_id=user.id).default_league_season_id = season.id
        repository.create_or_get_access(
            user_id=user.id,
            season_id=season.id,
            proof_mode=UserSeasonProofMode.PUBLIC,
            provider_connection_id=None,
            verified_at=NOW,
        )
    session.commit()

    session.execute(delete(User).where(User.id == user_a.id))
    session.commit()

    assert session.get(UserPreference, user_a.id) is None
    assert session.get(UserPreference, user_b.id).default_league_season_id == season.id
    assert [a.user_id for a in repository.list_eligible_access_for_season(season_id=season.id)] == [
        user_b.id
    ]
    assert session.get(League, league.id) is not None


def test_season_deletion_clears_default_and_cascades_access(committed) -> None:
    seeder, session = committed
    user_a, _, league, season = make_world(seeder, session)
    repository = LeagueAccessRepository(session)
    repository.get_or_create_preferences(user_id=user_a.id).default_league_season_id = season.id
    repository.create_or_get_access(
        user_id=user_a.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PUBLIC,
        provider_connection_id=None,
        verified_at=NOW,
    )
    session.commit()

    session.execute(delete(Season).where(Season.id == season.id))
    session.commit()
    session.expire_all()

    assert session.get(UserPreference, user_a.id).default_league_season_id is None
    assert repository.list_active_access_for_user(user_id=user_a.id) == []


def test_connection_backing_access_cannot_be_silently_deleted(committed) -> None:
    seeder, session = committed
    user_a, _, _, season = make_world(seeder, session)
    connection = seeder.connection(session, user_a)
    repository = LeagueAccessRepository(session)
    repository.create_or_get_access(
        user_id=user_a.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
        provider_connection_id=connection.id,
        verified_at=NOW,
    )
    session.commit()
    assert (
        repository.count_connection_access_usage(
            user_id=user_a.id, provider_connection_id=connection.id
        )
        == 1
    )

    with pytest.raises(IntegrityError):
        session.execute(delete(ProviderConnection).where(ProviderConnection.id == connection.id))
    session.rollback()
    assert session.get(ProviderConnection, connection.id) is not None
    assert (
        repository.list_active_access_for_user(user_id=user_a.id)[0].provider_connection_id
        == connection.id
    )


def test_user_deletion_cascades_user_owned_connection_and_its_access(committed) -> None:
    seeder, session = committed
    user_a, user_b, league, season = make_world(seeder, session)
    connection = seeder.connection(session, user_a)
    repository = LeagueAccessRepository(session)
    repository.create_or_get_access(
        user_id=user_a.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
        provider_connection_id=connection.id,
        verified_at=NOW,
    )
    repository.create_or_get_access(
        user_id=user_b.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PUBLIC,
        provider_connection_id=None,
        verified_at=NOW,
    )
    session.commit()

    connection_id, season_id, league_id, user_b_id = connection.id, season.id, league.id, user_b.id
    session.execute(delete(User).where(User.id == user_a.id))
    session.commit()
    session.expunge_all()

    assert session.get(ProviderConnection, connection_id) is None
    assert [a.user_id for a in repository.list_eligible_access_for_season(season_id=season_id)] == [
        user_b_id
    ]
    assert session.get(League, league_id) is not None


def test_connection_usage_is_isolated_per_owner(committed) -> None:
    seeder, session = committed
    user_a, user_b, _, season = make_world(seeder, session)
    connection = seeder.connection(session, user_a)
    repository = LeagueAccessRepository(session)
    repository.create_or_get_access(
        user_id=user_a.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
        provider_connection_id=connection.id,
        verified_at=NOW,
    )
    session.commit()

    assert (
        repository.count_connection_access_usage(
            user_id=user_b.id, provider_connection_id=connection.id
        )
        == 0
    )


def test_concurrent_access_creation_yields_one_row(committed) -> None:
    seeder, session = committed
    user_a, _, _, season = make_world(seeder, session)
    first = Session(engine)
    first_row = LeagueAccessRepository(first).create_or_get_access(
        user_id=user_a.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PUBLIC,
        provider_connection_id=None,
        verified_at=NOW,
    )

    def second_create() -> int:
        with Session(engine) as other:
            row = LeagueAccessRepository(other).create_or_get_access(
                user_id=user_a.id,
                season_id=season.id,
                proof_mode=UserSeasonProofMode.PUBLIC,
                provider_connection_id=None,
                verified_at=NOW,
            )
            other.commit()
            return row.id

    thread, result = run_in_thread(second_create)
    wait_for_lock_wait()
    first_id = first_row.id
    first.commit()
    first.close()
    thread.join(timeout=10)

    assert result.get("value") == first_id
    count = session.scalar(
        select(func.count())
        .select_from(UserLeagueSeasonAccess)
        .where(UserLeagueSeasonAccess.user_id == user_a.id)
    )
    assert count == 1


def test_orphan_mark_then_access_creation_ends_not_orphaned(committed) -> None:
    seeder, session = committed
    user_a, _, league, season = make_world(seeder, session)
    marker = Session(engine)
    assert LeagueAccessRepository(marker).mark_league_orphaned(league_id=league.id, orphaned_at=NOW)

    def create() -> int:
        with Session(engine) as other:
            LeagueAccessRepository(other).create_or_get_access(
                user_id=user_a.id,
                season_id=season.id,
                proof_mode=UserSeasonProofMode.PUBLIC,
                provider_connection_id=None,
                verified_at=NOW,
            )
            other.commit()
            return 1

    thread, result = run_in_thread(create)
    wait_for_lock_wait()
    marker.commit()
    marker.close()
    thread.join(timeout=10)

    assert result.get("value") == 1
    session.expire_all()
    assert session.get(League, league.id).orphaned_at is None
    assert len(LeagueAccessRepository(session).list_active_access_for_user(user_id=user_a.id)) == 1


def test_access_creation_then_orphan_mark_refuses_to_orphan(committed) -> None:
    seeder, session = committed
    user_a, _, league, season = make_world(seeder, session)
    creator = Session(engine)
    LeagueAccessRepository(creator).create_or_get_access(
        user_id=user_a.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PUBLIC,
        provider_connection_id=None,
        verified_at=NOW,
    )

    def mark() -> bool:
        with Session(engine) as other:
            marked = LeagueAccessRepository(other).mark_league_orphaned(
                league_id=league.id, orphaned_at=NOW
            )
            other.commit()
            return marked

    thread, result = run_in_thread(mark)
    wait_for_lock_wait()
    creator.commit()
    creator.close()
    thread.join(timeout=10)

    assert result.get("value") is False
    session.expire_all()
    assert session.get(League, league.id).orphaned_at is None


def test_workspace_era_rows_remain_valid_and_unmarked(committed) -> None:
    seeder, session = committed
    installation = Installation(application_name="Legacy Workspace")
    session.add(installation)
    session.flush()
    seeder.installation_ids.append(installation.id)
    connection = ProviderConnection(
        installation_id=installation.id,
        provider=FantasyProvider.ESPN,
        display_name="Legacy",
    )
    league = seeder.league(session)
    season = seeder.season(session, league)
    session.add(connection)
    session.commit()

    assert connection.user_id is None
    assert season.sync_state is None
    assert season.data_version == 0
    assert league.orphaned_at is None
    session.delete(installation)
    session.commit()
