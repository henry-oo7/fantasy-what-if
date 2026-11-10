from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import Matchup
from app.db.session import engine
from app.providers.domain import ProviderMatchup
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


def matchup_count(
    session: Session,
    *,
    week: int,
) -> int:
    value = session.scalar(
        select(func.count())
        .select_from(Matchup)
        .where(
            Matchup.season_id == 1,
            Matchup.week == week,
        )
    )

    return int(value or 0)


def test_matchup_replacement_is_idempotent(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )
    team_ids = sorted(repository.get_team_map(season_id=1))

    assert len(team_ids) == 12

    provider_matchups = (
        ProviderMatchup(
            week=1,
            home_provider_team_id=team_ids[0],
            away_provider_team_id=team_ids[1],
            home_score=Decimal("120.50"),
            away_score=Decimal("110.25"),
            is_complete=True,
            is_bye=False,
        ),
        ProviderMatchup(
            week=1,
            home_provider_team_id=team_ids[2],
            away_provider_team_id=None,
            home_score=None,
            away_score=None,
            is_complete=True,
            is_bye=True,
        ),
    )

    first = repository.replace_matchups(
        context=context,
        provider_matchups=provider_matchups,
        requested_weeks=(1,),
    )
    database_session.flush()

    assert len(first) == 2
    assert (
        matchup_count(
            database_session,
            week=1,
        )
        == 2
    )

    second = repository.replace_matchups(
        context=context,
        provider_matchups=provider_matchups,
        requested_weeks=(1,),
    )
    database_session.flush()

    assert len(second) == 2
    assert (
        matchup_count(
            database_session,
            week=1,
        )
        == 2
    )

    stored = database_session.scalars(
        select(Matchup)
        .where(
            Matchup.season_id == 1,
            Matchup.week == 1,
        )
        .order_by(Matchup.home_team_id)
    ).all()

    assert len(stored) == 2
    assert any(matchup.is_bye and matchup.away_team_id is None for matchup in stored)
    assert any(
        not matchup.is_bye
        and matchup.home_score == Decimal("120.50")
        and matchup.away_score == Decimal("110.25")
        for matchup in stored
    )


def test_unknown_team_is_rejected_before_deletion(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    before_count = matchup_count(
        database_session,
        week=1,
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="unknown away team",
    ):
        repository.replace_matchups(
            context=context,
            provider_matchups=(
                ProviderMatchup(
                    week=1,
                    home_provider_team_id="1",
                    away_provider_team_id="unknown",
                    home_score=Decimal("100"),
                    away_score=Decimal("90"),
                    is_complete=True,
                    is_bye=False,
                ),
            ),
            requested_weeks=(1,),
        )

    after_count = matchup_count(
        database_session,
        week=1,
    )

    assert after_count == before_count


def test_unrequested_week_is_rejected_before_deletion(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    before_count = matchup_count(
        database_session,
        week=1,
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="unrequested week",
    ):
        repository.replace_matchups(
            context=context,
            provider_matchups=(
                ProviderMatchup(
                    week=2,
                    home_provider_team_id="1",
                    away_provider_team_id="2",
                    home_score=Decimal("100"),
                    away_score=Decimal("90"),
                    is_complete=True,
                    is_bye=False,
                ),
            ),
            requested_weeks=(1,),
        )

    assert (
        matchup_count(
            database_session,
            week=1,
        )
        == before_count
    )


def test_empty_matchup_collection_clears_requested_week(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    assert (
        matchup_count(
            database_session,
            week=1,
        )
        > 0
    )

    result = repository.replace_matchups(
        context=context,
        provider_matchups=(),
        requested_weeks=(1,),
    )
    database_session.flush()

    assert result == ()
    assert (
        matchup_count(
            database_session,
            week=1,
        )
        == 0
    )


@pytest.mark.parametrize(
    "requested_weeks",
    [
        (0,),
        (-1,),
    ],
)
def test_requested_weeks_must_be_positive(
    database_session: Session,
    requested_weeks: tuple[int, ...],
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    with pytest.raises(ValueError):
        repository.replace_matchups(
            context=context,
            provider_matchups=(),
            requested_weeks=requested_weeks,
        )
