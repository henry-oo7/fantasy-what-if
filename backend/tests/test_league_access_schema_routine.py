"""Routine tests for the canonical League access schema foundation (ADR 0010)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import Installation, ProviderConnection
from app.db.session import engine
from app.repositories.league_access_repository import LeagueAccessRepository

SYNTHETIC_CIPHERTEXT = "synthetic-ciphertext-not-real"
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()

        if transaction.is_active:
            transaction.rollback()

        connection.close()


def make_user(session: Session) -> User:
    token = uuid4().hex[:12]
    user = User(
        username=f"acc-{token}",
        display_name="Synthetic User",
        password_hash="synthetic-hash-not-real",
        role=UserRole.SPECTATOR,
    )
    session.add(user)
    session.flush()
    return user


def make_installation(session: Session) -> Installation:
    slug = f"acc-{uuid4().hex[:12]}"
    installation = Installation(
        application_name="Access Schema Test",
        slug=slug,
        normalized_slug=slug,
        timezone="America/Chicago",
        status=InstallationStatus.SETUP_IN_PROGRESS,
    )
    session.add(installation)
    session.flush()
    return installation


def make_league(
    session: Session,
    *,
    provider: FantasyProvider = FantasyProvider.ESPN,
    provider_league_id: str | None = None,
) -> League:
    league = League(
        name="Synthetic League",
        provider=provider,
        provider_league_id=provider_league_id or uuid4().hex[:16],
    )
    session.add(league)
    session.flush()
    return league


def make_season(session: Session, league: League | None = None) -> Season:
    league = league or make_league(session)
    season = Season(
        league_id=league.id,
        year=2025,
        current_week=1,
        regular_season_weeks=14,
    )
    session.add(season)
    session.flush()
    return season


def make_connection(
    session: Session,
    *,
    user: User | None = None,
    encrypted: bool = False,
) -> ProviderConnection:
    connection = ProviderConnection(
        user_id=user.id if user else None,
        installation_id=None if user else make_installation(session).id,
        provider=FantasyProvider.ESPN,
        display_name="ESPN",
        is_active=False,
        encrypted_credentials=SYNTHETIC_CIPHERTEXT if encrypted else None,
        credential_version=1 if encrypted else None,
    )
    session.add(connection)
    session.flush()
    return connection


def make_access(
    session: Session,
    *,
    user: User,
    season: Season,
    status: UserSeasonAccessStatus = UserSeasonAccessStatus.ACTIVE,
    proof_mode: UserSeasonProofMode = UserSeasonProofMode.PUBLIC,
    connection: ProviderConnection | None = None,
) -> UserLeagueSeasonAccess:
    access = UserLeagueSeasonAccess(
        user_id=user.id,
        season_id=season.id,
        provider_connection_id=connection.id if connection else None,
        proof_mode=proof_mode,
        status=status,
        verified_at=NOW,
    )
    session.add(access)
    session.flush()
    return access


# 1. Enum values


def test_season_sync_state_values() -> None:
    assert [member.value for member in SeasonSyncState] == [
        "never_synced",
        "queued",
        "syncing",
        "fresh",
        "stale",
        "failed",
    ]


def test_user_season_access_status_values() -> None:
    assert [member.value for member in UserSeasonAccessStatus] == [
        "active",
        "reverification_required",
        "revoked",
    ]


def test_user_season_proof_mode_values() -> None:
    assert [member.value for member in UserSeasonProofMode] == [
        "public",
        "private_credentials",
    ]


# 2. Schema introspection


def test_user_preferences_columns() -> None:
    columns = {c["name"]: c for c in inspect(engine).get_columns("user_preferences")}

    assert {"user_id", "default_league_season_id", "created_at", "updated_at"} <= set(columns)
    assert columns["user_id"]["nullable"] is False
    assert columns["default_league_season_id"]["nullable"] is True
    assert columns["created_at"]["nullable"] is False
    assert columns["updated_at"]["nullable"] is False


def test_user_league_season_access_columns() -> None:
    columns = {c["name"]: c for c in inspect(engine).get_columns("user_league_season_access")}

    expected_nullability = {
        "id": False,
        "user_id": False,
        "season_id": False,
        "provider_connection_id": True,
        "proof_mode": False,
        "status": False,
        "verified_at": False,
        "last_reverified_at": True,
        "created_at": False,
        "updated_at": False,
    }
    assert set(expected_nullability) <= set(columns)
    for name, nullable in expected_nullability.items():
        assert columns[name]["nullable"] is nullable, name


@pytest.mark.parametrize(
    ("table", "index_name"),
    [
        ("leagues", "uq_leagues_provider_identity"),
        ("leagues", "ix_leagues_orphaned_at"),
        ("user_league_season_access", "ix_user_league_season_access_active_season"),
        ("user_league_season_access", "ix_user_league_season_access_connection"),
        ("provider_connections", "ix_provider_connections_user_id"),
        ("user_preferences", "ix_user_preferences_default_league_season_id"),
    ],
)
def test_expected_indexes_exist(table: str, index_name: str) -> None:
    names = {index["name"] for index in inspect(engine).get_indexes(table)}

    assert index_name in names


def test_canonical_league_identity_index_is_unique() -> None:
    indexes = {index["name"]: index for index in inspect(engine).get_indexes("leagues")}

    identity = indexes["uq_leagues_provider_identity"]
    assert identity["unique"] is True
    assert identity["column_names"] == ["provider", "provider_league_id"]


def test_seasons_data_version_is_not_null() -> None:
    columns = {c["name"]: c for c in inspect(engine).get_columns("seasons")}

    assert columns["data_version"]["nullable"] is False
    assert columns["sync_state"]["nullable"] is True
    assert columns["last_sync_failure_code"]["nullable"] is True


def test_provider_connection_installation_id_is_nullable() -> None:
    columns = {c["name"]: c for c in inspect(engine).get_columns("provider_connections")}

    assert columns["installation_id"]["nullable"] is True
    assert columns["user_id"]["nullable"] is True


# 3. Constraint rejections


def assert_rejected(session: Session, action: Any) -> None:
    with pytest.raises((IntegrityError, DataError)), session.begin_nested():
        action()
        session.flush()


def test_public_access_with_connection_is_rejected(database_session: Session) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    connection = make_connection(database_session, user=user)

    assert_rejected(
        database_session,
        lambda: make_access(
            database_session,
            user=user,
            season=season,
            proof_mode=UserSeasonProofMode.PUBLIC,
            connection=connection,
        ),
    )


def test_private_access_without_connection_is_rejected(database_session: Session) -> None:
    user = make_user(database_session)
    season = make_season(database_session)

    assert_rejected(
        database_session,
        lambda: make_access(
            database_session,
            user=user,
            season=season,
            proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
            connection=None,
        ),
    )


def test_duplicate_user_season_access_is_rejected(database_session: Session) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    make_access(database_session, user=user, season=season)

    assert_rejected(
        database_session,
        lambda: make_access(database_session, user=user, season=season),
    )


def test_negative_data_version_is_rejected(database_session: Session) -> None:
    season = make_season(database_session)

    def update() -> None:
        season.data_version = -1

    assert_rejected(database_session, update)


@pytest.mark.parametrize("code", ["Bad Code!", "a" * 65], ids=["unsafe-chars", "too-long"])
def test_unsafe_sync_failure_code_is_rejected(database_session: Session, code: str) -> None:
    season = make_season(database_session)

    def update() -> None:
        season.last_sync_failure_code = code

    assert_rejected(database_session, update)


def test_connection_without_any_owner_is_rejected(database_session: Session) -> None:
    def create() -> None:
        database_session.add(
            ProviderConnection(
                installation_id=None,
                user_id=None,
                provider=FantasyProvider.ESPN,
                display_name="ESPN",
                is_active=False,
            )
        )

    assert_rejected(database_session, create)


def test_user_owned_connection_without_installation_is_valid(
    database_session: Session,
) -> None:
    user = make_user(database_session)

    connection = make_connection(database_session, user=user)

    assert connection.installation_id is None
    assert connection.user_id == user.id


def test_invalid_enum_value_is_rejected_by_database(database_session: Session) -> None:
    season = make_season(database_session)

    with pytest.raises((IntegrityError, DataError)), database_session.begin_nested():
        database_session.execute(
            text("UPDATE seasons SET sync_state = 'bogus_state' WHERE id = :id"),
            {"id": season.id},
        )


# 4. Repository


def test_get_or_create_preferences_is_idempotent(database_session: Session) -> None:
    user = make_user(database_session)
    repository = LeagueAccessRepository(database_session)

    first = repository.get_or_create_preferences(user_id=user.id)
    second = repository.get_or_create_preferences(user_id=user.id)

    assert first is second
    assert first.user_id == user.id
    assert first.default_league_season_id is None
    assert (
        database_session.query(UserPreference).filter(UserPreference.user_id == user.id).count()
        == 1
    )


def test_preferences_are_isolated_per_user(database_session: Session) -> None:
    first_user = make_user(database_session)
    second_user = make_user(database_session)
    season = make_season(database_session)
    repository = LeagueAccessRepository(database_session)

    first = repository.get_or_create_preferences(user_id=first_user.id)
    first.default_league_season_id = season.id
    database_session.flush()
    second = repository.get_or_create_preferences(user_id=second_user.id)

    assert second.user_id == second_user.id
    assert second.default_league_season_id is None
    assert first.default_league_season_id == season.id


def test_get_league_by_identity_matches_provider_and_stripped_id(
    database_session: Session,
) -> None:
    provider_league_id = uuid4().hex[:16]
    league = make_league(database_session, provider_league_id=provider_league_id)
    repository = LeagueAccessRepository(database_session)

    found = repository.get_league_by_identity(
        provider=FantasyProvider.ESPN,
        provider_league_id=f"  {provider_league_id}  ",
    )

    assert found is not None
    assert found.id == league.id


def test_get_league_by_identity_returns_none_for_other_provider_or_unknown(
    database_session: Session,
) -> None:
    provider_league_id = uuid4().hex[:16]
    make_league(database_session, provider_league_id=provider_league_id)
    repository = LeagueAccessRepository(database_session)

    assert (
        repository.get_league_by_identity(
            provider=FantasyProvider.SLEEPER, provider_league_id=provider_league_id
        )
        is None
    )
    assert (
        repository.get_league_by_identity(
            provider=FantasyProvider.ESPN, provider_league_id=uuid4().hex[:16]
        )
        is None
    )


def test_create_or_get_access_creates_active_row(database_session: Session) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    repository = LeagueAccessRepository(database_session)

    access = repository.create_or_get_access(
        user_id=user.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PUBLIC,
        provider_connection_id=None,
        verified_at=NOW,
    )

    assert access.id is not None
    assert access.status is UserSeasonAccessStatus.ACTIVE
    assert access.proof_mode is UserSeasonProofMode.PUBLIC
    assert access.provider_connection_id is None
    assert access.verified_at == NOW


def test_create_or_get_access_returns_existing_row_unchanged(
    database_session: Session,
) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    connection = make_connection(database_session, user=user)
    existing = make_access(
        database_session,
        user=user,
        season=season,
        status=UserSeasonAccessStatus.REVERIFICATION_REQUIRED,
    )
    repository = LeagueAccessRepository(database_session)

    returned = repository.create_or_get_access(
        user_id=user.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
        provider_connection_id=connection.id,
        verified_at=NOW + timedelta(days=1),
    )

    assert returned.id == existing.id
    assert returned.status is UserSeasonAccessStatus.REVERIFICATION_REQUIRED
    assert returned.proof_mode is UserSeasonProofMode.PUBLIC
    assert returned.provider_connection_id is None
    assert returned.verified_at == NOW


def test_create_or_get_access_twice_yields_one_row(database_session: Session) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    repository = LeagueAccessRepository(database_session)
    arguments: dict[str, Any] = {
        "user_id": user.id,
        "season_id": season.id,
        "proof_mode": UserSeasonProofMode.PUBLIC,
        "provider_connection_id": None,
        "verified_at": NOW,
    }

    first = repository.create_or_get_access(**arguments)
    second = repository.create_or_get_access(**arguments)

    assert first.id == second.id
    assert len(repository.list_active_access_for_user(user_id=user.id)) == 1


def test_list_active_access_for_user_filters_status_and_user(
    database_session: Session,
) -> None:
    user = make_user(database_session)
    other_user = make_user(database_session)
    active_season = make_season(database_session)
    reverify_season = make_season(database_session)
    revoked_season = make_season(database_session)
    active = make_access(database_session, user=user, season=active_season)
    make_access(
        database_session,
        user=user,
        season=reverify_season,
        status=UserSeasonAccessStatus.REVERIFICATION_REQUIRED,
    )
    make_access(
        database_session,
        user=user,
        season=revoked_season,
        status=UserSeasonAccessStatus.REVOKED,
    )
    make_access(database_session, user=other_user, season=active_season)

    rows = LeagueAccessRepository(database_session).list_active_access_for_user(user_id=user.id)

    assert [row.id for row in rows] == [active.id]


def test_list_eligible_access_for_season_filters_status_and_season(
    database_session: Session,
) -> None:
    season = make_season(database_session)
    other_season = make_season(database_session)
    active_user = make_user(database_session)
    reverify_user = make_user(database_session)
    revoked_user = make_user(database_session)
    other_season_user = make_user(database_session)
    active = make_access(database_session, user=active_user, season=season)
    make_access(
        database_session,
        user=reverify_user,
        season=season,
        status=UserSeasonAccessStatus.REVERIFICATION_REQUIRED,
    )
    make_access(
        database_session,
        user=revoked_user,
        season=season,
        status=UserSeasonAccessStatus.REVOKED,
    )
    make_access(database_session, user=other_season_user, season=other_season)

    rows = LeagueAccessRepository(database_session).list_eligible_access_for_season(
        season_id=season.id
    )

    assert [row.id for row in rows] == [active.id]


def test_mark_league_orphaned_sets_timestamp_without_active_access(
    database_session: Session,
) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    make_access(
        database_session,
        user=user,
        season=season,
        status=UserSeasonAccessStatus.REVOKED,
    )
    repository = LeagueAccessRepository(database_session)

    result = repository.mark_league_orphaned(league_id=season.league_id, orphaned_at=NOW)

    league = database_session.get(League, season.league_id)
    assert result is True
    assert league is not None
    assert league.orphaned_at == NOW


def test_mark_league_orphaned_keeps_first_timestamp(database_session: Session) -> None:
    season = make_season(database_session)
    repository = LeagueAccessRepository(database_session)

    repository.mark_league_orphaned(league_id=season.league_id, orphaned_at=NOW)
    result = repository.mark_league_orphaned(
        league_id=season.league_id, orphaned_at=NOW + timedelta(days=3)
    )

    league = database_session.get(League, season.league_id)
    assert result is True
    assert league is not None
    assert league.orphaned_at == NOW


def test_mark_league_orphaned_refuses_when_active_access_exists(
    database_session: Session,
) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    make_access(database_session, user=user, season=season)
    repository = LeagueAccessRepository(database_session)

    result = repository.mark_league_orphaned(league_id=season.league_id, orphaned_at=NOW)

    league = database_session.get(League, season.league_id)
    assert result is False
    assert league is not None
    assert league.orphaned_at is None


def test_mark_league_orphaned_returns_false_for_unknown_league(
    database_session: Session,
) -> None:
    repository = LeagueAccessRepository(database_session)

    assert repository.mark_league_orphaned(league_id=2_000_000_000, orphaned_at=NOW) is False


def test_clear_league_orphaned_clears_timestamp(database_session: Session) -> None:
    season = make_season(database_session)
    repository = LeagueAccessRepository(database_session)
    repository.mark_league_orphaned(league_id=season.league_id, orphaned_at=NOW)

    repository.clear_league_orphaned(league_id=season.league_id)

    league = database_session.get(League, season.league_id)
    assert league is not None
    database_session.refresh(league)
    assert league.orphaned_at is None


def test_new_active_access_clears_orphaned_at(database_session: Session) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    repository = LeagueAccessRepository(database_session)
    repository.mark_league_orphaned(league_id=season.league_id, orphaned_at=NOW)
    database_session.flush()

    repository.create_or_get_access(
        user_id=user.id,
        season_id=season.id,
        proof_mode=UserSeasonProofMode.PUBLIC,
        provider_connection_id=None,
        verified_at=NOW,
    )
    database_session.flush()

    league = database_session.get(League, season.league_id)
    assert league is not None
    assert league.orphaned_at is None


def test_count_connection_access_usage_counts_for_owner_only(
    database_session: Session,
) -> None:
    owner = make_user(database_session)
    other_user = make_user(database_session)
    connection = make_connection(database_session, user=owner, encrypted=True)
    for _ in range(2):
        make_access(
            database_session,
            user=owner,
            season=make_season(database_session),
            proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
            connection=connection,
        )
    repository = LeagueAccessRepository(database_session)

    assert (
        repository.count_connection_access_usage(
            user_id=owner.id, provider_connection_id=connection.id
        )
        == 2
    )
    assert (
        repository.count_connection_access_usage(
            user_id=other_user.id, provider_connection_id=connection.id
        )
        == 0
    )


def test_count_connection_access_usage_is_zero_without_usage(
    database_session: Session,
) -> None:
    owner = make_user(database_session)
    connection = make_connection(database_session, user=owner)

    assert (
        LeagueAccessRepository(database_session).count_connection_access_usage(
            user_id=owner.id, provider_connection_id=connection.id
        )
        == 0
    )


# 5. repr hygiene


def test_access_and_preference_repr_contain_no_credential_text(
    database_session: Session,
) -> None:
    user = make_user(database_session)
    season = make_season(database_session)
    connection = make_connection(database_session, user=user, encrypted=True)
    access = make_access(
        database_session,
        user=user,
        season=season,
        proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
        connection=connection,
    )
    preference = LeagueAccessRepository(database_session).get_or_create_preferences(user_id=user.id)

    for value in (access, preference):
        assert SYNTHETIC_CIPHERTEXT not in repr(value)
        assert "synthetic-hash-not-real" not in repr(value)
