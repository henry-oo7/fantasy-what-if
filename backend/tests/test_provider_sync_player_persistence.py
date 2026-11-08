from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import PlayerWeekScore
from app.db.session import engine
from app.providers.domain import (
    ProviderPlayerWeek,
    ProviderWeeklyRoster,
)
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


def make_player(
    *,
    provider_player_id: str = "999001",
    player_name: str = "Canonical Player",
) -> ProviderPlayerWeek:
    return ProviderPlayerWeek(
        provider_player_id=provider_player_id,
        player_name=player_name,
        pro_team="HOU",
        provider_position="WR",
        canonical_position="WR",
        actual_lineup_slot="WR",
        eligible_lineup_slots=(
            "WR",
            "RB/WR/TE",
            "BE",
        ),
        points=Decimal("18.50"),
        projected_points=Decimal("16.75"),
        is_starter=True,
        is_bench=False,
        is_injured_reserve=False,
        is_on_roster=True,
        metadata={
            "provider": "espn",
            "injury_status": "ACTIVE",
            "is_nfl_bye": False,
        },
    )


def player_week_count(
    session: Session,
    *,
    week: int | None = None,
) -> int:
    statement = (
        select(func.count()).select_from(PlayerWeekScore).where(PlayerWeekScore.season_id == 1)
    )

    if week is not None:
        statement = statement.where(PlayerWeekScore.week == week)

    return int(session.scalar(statement) or 0)


def test_player_week_replacement_is_idempotent(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )
    provider_team_id = sorted(repository.get_team_map(season_id=1))[0]

    rosters = (
        ProviderWeeklyRoster(
            provider_team_id=provider_team_id,
            week=1,
            players=(
                make_player(
                    provider_player_id="999001",
                ),
                make_player(
                    provider_player_id="999002",
                    player_name="Second Player",
                ),
            ),
        ),
    )

    first = repository.replace_player_week_scores(
        context=context,
        provider_rosters=rosters,
        requested_weeks=(1,),
    )
    database_session.flush()

    assert len(first) == 2
    assert (
        player_week_count(
            database_session,
            week=1,
        )
        == 2
    )

    second = repository.replace_player_week_scores(
        context=context,
        provider_rosters=rosters,
        requested_weeks=(1,),
    )
    database_session.flush()

    assert len(second) == 2
    assert (
        player_week_count(
            database_session,
            week=1,
        )
        == 2
    )

    stored = database_session.scalars(
        select(PlayerWeekScore)
        .where(
            PlayerWeekScore.season_id == 1,
            PlayerWeekScore.week == 1,
        )
        .order_by(PlayerWeekScore.provider_player_id)
    ).all()

    assert len(stored) == 2

    first_player = stored[0]

    assert first_player.provider_player_id == "999001"
    assert first_player.provider_position == "WR"
    assert first_player.default_position == "WR"
    assert first_player.actual_lineup_slot == "WR"
    assert first_player.eligible_lineup_slots == [
        "WR",
        "RB/WR/TE",
        "BE",
    ]
    assert first_player.points == Decimal("18.50")
    assert first_player.projected_points == Decimal("16.75")
    assert first_player.was_on_roster is True
    assert first_player.is_starter is True
    assert first_player.is_bench is False
    assert first_player.is_injured_reserve is False
    assert first_player.raw_metadata["canonical_position"] == "WR"
    assert first_player.raw_metadata["injury_status"] == "ACTIVE"


def test_defense_preserves_provider_position(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )
    provider_team_id = sorted(repository.get_team_map(season_id=1))[0]

    defense = ProviderPlayerWeek(
        provider_player_id="-16001",
        player_name="Falcons D/ST",
        pro_team="ATL",
        provider_position="D/ST",
        canonical_position="DST",
        actual_lineup_slot="D/ST",
        eligible_lineup_slots=(
            "D/ST",
            "BE",
            "IR",
        ),
        points=Decimal("8.00"),
        projected_points=Decimal("6.50"),
        is_starter=True,
        is_bench=False,
        is_injured_reserve=False,
        is_on_roster=True,
        metadata={
            "provider": "espn",
        },
    )

    stored = repository.replace_player_week_scores(
        context=context,
        provider_rosters=(
            ProviderWeeklyRoster(
                provider_team_id=provider_team_id,
                week=1,
                players=(defense,),
            ),
        ),
        requested_weeks=(1,),
    )

    assert len(stored) == 1
    assert stored[0].provider_position == "D/ST"
    assert stored[0].default_position == "D/ST"
    assert stored[0].raw_metadata["canonical_position"] == "DST"


def test_unknown_team_is_rejected_before_deletion(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    before_count = player_week_count(
        database_session,
        week=1,
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="unknown team",
    ):
        repository.replace_player_week_scores(
            context=context,
            provider_rosters=(
                ProviderWeeklyRoster(
                    provider_team_id="unknown",
                    week=1,
                    players=(make_player(),),
                ),
            ),
            requested_weeks=(1,),
        )

    assert (
        player_week_count(
            database_session,
            week=1,
        )
        == before_count
    )


def test_unrequested_week_is_rejected_before_deletion(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )
    provider_team_id = sorted(repository.get_team_map(season_id=1))[0]

    before_count = player_week_count(
        database_session,
        week=1,
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="unrequested week",
    ):
        repository.replace_player_week_scores(
            context=context,
            provider_rosters=(
                ProviderWeeklyRoster(
                    provider_team_id=(provider_team_id),
                    week=2,
                    players=(make_player(),),
                ),
            ),
            requested_weeks=(1,),
        )

    assert (
        player_week_count(
            database_session,
            week=1,
        )
        == before_count
    )


def test_nonnumeric_provider_player_id_is_preserved(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )
    provider_team_id = sorted(repository.get_team_map(season_id=1))[0]

    stored = repository.replace_player_week_scores(
        context=context,
        provider_rosters=(
            ProviderWeeklyRoster(
                provider_team_id=provider_team_id,
                week=1,
                players=(
                    make_player(
                        provider_player_id=("player-alpha"),
                    ),
                ),
            ),
        ),
        requested_weeks=(1,),
    )

    database_session.flush()

    assert len(stored) == 1
    assert stored[0].provider_player_id == "player-alpha"


def test_duplicate_team_week_is_rejected_before_deletion(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )
    provider_team_id = sorted(repository.get_team_map(season_id=1))[0]

    roster = ProviderWeeklyRoster(
        provider_team_id=provider_team_id,
        week=1,
        players=(make_player(),),
    )

    before_count = player_week_count(
        database_session,
        week=1,
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="duplicate roster data",
    ):
        repository.replace_player_week_scores(
            context=context,
            provider_rosters=(
                roster,
                roster,
            ),
            requested_weeks=(1,),
        )

    assert (
        player_week_count(
            database_session,
            week=1,
        )
        == before_count
    )


def test_empty_roster_collection_clears_requested_week(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    assert (
        player_week_count(
            database_session,
            week=1,
        )
        > 0
    )

    result = repository.replace_player_week_scores(
        context=context,
        provider_rosters=(),
        requested_weeks=(1,),
    )
    database_session.flush()

    assert result == ()
    assert (
        player_week_count(
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
        repository.replace_player_week_scores(
            context=context,
            provider_rosters=(),
            requested_weeks=requested_weeks,
        )
