from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import Team
from app.db.session import engine
from app.providers.domain import ProviderTeam
from app.repositories.provider_sync_repository import (
    ProviderSyncContextError,
    ProviderSyncRepository,
)


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
        transaction.rollback()
        connection.close()


def make_provider_team(
    *,
    provider_team_id: str = "1",
    name: str = "Canonical Team",
    wins: int = 5,
) -> ProviderTeam:
    return ProviderTeam(
        provider_team_id=provider_team_id,
        name=name,
        abbreviation="CT",
        owner_display_name="League Member",
        logo_url="https://example.invalid/team.png",
        wins=wins,
        losses=3,
        ties=0,
        points_for=Decimal("1012.34"),
        points_against=Decimal("998.76"),
        is_active=True,
        metadata={
            "source": "test",
        },
    )


def test_canonical_team_upsert_is_idempotent(
    database_session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    original_team = database_session.scalar(
        select(Team)
        .where(
            Team.season_id == 1,
        )
        .order_by(Team.id)
        .limit(1)
    )

    assert original_team is not None
    provider_team_id = original_team.provider_team_id
    assert provider_team_id is not None

    before_count = database_session.scalar(
        select(Team)
        .where(Team.season_id == 1)
        .with_only_columns(__import__("sqlalchemy").func.count())
    )

    first = repository.upsert_team(
        context=context,
        provider_team=make_provider_team(
            provider_team_id=provider_team_id,
            name="Canonical Updated Team",
            wins=7,
        ),
    )
    database_session.flush()

    second = repository.upsert_team(
        context=context,
        provider_team=make_provider_team(
            provider_team_id=provider_team_id,
            name="Canonical Updated Again",
            wins=8,
        ),
    )
    database_session.flush()

    after_count = database_session.scalar(
        select(Team)
        .where(Team.season_id == 1)
        .with_only_columns(__import__("sqlalchemy").func.count())
    )

    assert first.id == original_team.id
    assert second.id == original_team.id
    assert before_count == 12
    assert after_count == 12

    refreshed = database_session.get_one(
        Team,
        original_team.id,
    )
    database_session.refresh(refreshed)

    assert refreshed.provider_team_id == provider_team_id
    assert refreshed.name == "Canonical Updated Again"
    assert refreshed.abbreviation == "CT"
    assert refreshed.owner_display_name == "League Member"
    assert refreshed.wins == 8
    assert refreshed.points_for == Decimal("1012.34")


def test_team_collection_rejects_duplicate_provider_ids(
    database_session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    team = make_provider_team(
        provider_team_id="1",
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="duplicate provider IDs",
    ):
        repository.upsert_teams(
            context=context,
            provider_teams=(
                team,
                team,
            ),
        )
