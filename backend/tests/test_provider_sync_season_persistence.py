from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import ScheduleRule
from app.db.platform_enums import FantasySport
from app.db.session import engine
from app.providers.domain import (
    ProviderCapabilities,
    ProviderLeague,
    ProviderScheduleRules,
    ProviderTeam,
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


def make_team(
    provider_team_id: str,
) -> ProviderTeam:
    return ProviderTeam(
        provider_team_id=provider_team_id,
        name=f"Team {provider_team_id}",
        wins=0,
        losses=0,
        ties=0,
        points_for=Decimal("0"),
        points_against=Decimal("0"),
    )


def make_provider_league(
    *,
    provider_league_id: str = "529250970",
    provider_season_id: str = "2026",
) -> ProviderLeague:
    teams = tuple(make_team(str(team_id)) for team_id in range(1, 13))

    schedule_rules = ProviderScheduleRules(
        regular_season_weeks=12,
        playoff_week_start=13,
        playoff_week_end=16,
        has_divisions=True,
        has_median_game=False,
        has_doubleheaders=False,
        allows_ties=True,
        metadata={
            "provider": "espn",
            "configuration_complete": False,
        },
    )

    capabilities = ProviderCapabilities(
        supports_head_to_head=True,
        supports_weekly_matchups=True,
        supports_lineup_history=True,
        supports_optimal_lineups=False,
        supports_schedule_swap=True,
        supports_all_play=True,
        supports_league_average=True,
        supports_team_logos=False,
    )

    return ProviderLeague(
        provider_league_id=provider_league_id,
        provider_season_id=provider_season_id,
        name="Canonical Jimmy and Friends",
        sport=FantasySport.FOOTBALL,
        season_year=2026,
        current_week=10,
        regular_season_weeks=12,
        playoff_week_start=13,
        playoff_week_end=16,
        scoring_type="head_to_head_points",
        logo_url=None,
        teams=teams,
        lineup_slots=(),
        scoring_rules=(),
        schedule_rules=schedule_rules,
        capabilities=capabilities,
        metadata={
            "provider": "espn",
        },
    )


def schedule_rule_count(
    session: Session,
) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(ScheduleRule).where(ScheduleRule.season_id == 1)
        )
        or 0
    )


def test_season_configuration_update_is_idempotent(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )
    provider_league = make_provider_league()

    repository.update_season_configuration(
        context=context,
        provider_league=provider_league,
    )
    database_session.flush()

    repository.update_season_configuration(
        context=context,
        provider_league=provider_league,
    )
    database_session.flush()

    assert schedule_rule_count(database_session) == 1

    assert context.league.name == "Canonical Jimmy and Friends"
    assert context.season.current_week == 10
    assert context.season.regular_season_weeks == 12
    assert context.season.playoff_week_start == 13
    assert context.season.playoff_week_end == 16
    assert context.season.team_count == 12
    assert context.season.scoring_type == "head_to_head_points"

    assert context.season.supports_head_to_head is True
    assert context.season.supports_lineup_history is True
    assert context.season.supports_optimal_lineups is False
    assert context.season.supports_schedule_swap is True
    assert context.season.supports_team_logos is False

    rule = database_session.scalar(select(ScheduleRule).where(ScheduleRule.season_id == 1))

    assert rule is not None
    assert rule.regular_season_weeks == 12
    assert rule.playoff_week_start == 13
    assert rule.playoff_week_end == 16
    assert rule.has_divisions is True
    assert rule.has_median_game is False
    assert rule.has_doubleheaders is False
    assert rule.allows_ties is True
    assert rule.metadata_payload == {
        "provider": "espn",
        "configuration_complete": False,
    }


def test_mismatched_league_id_is_rejected(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    original_name = context.league.name

    with pytest.raises(
        ProviderSyncContextError,
        match="league identity",
    ):
        repository.update_season_configuration(
            context=context,
            provider_league=make_provider_league(
                provider_league_id="different",
            ),
        )

    assert context.league.name == original_name


def test_mismatched_season_id_is_rejected(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    with pytest.raises(
        ProviderSyncContextError,
        match="season identity",
    ):
        repository.update_season_configuration(
            context=context,
            provider_league=make_provider_league(
                provider_season_id="2027",
            ),
        )


def test_schedule_rule_upsert_updates_existing_row(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    first = ProviderScheduleRules(
        regular_season_weeks=12,
        playoff_week_start=13,
        playoff_week_end=16,
        has_divisions=False,
        has_median_game=False,
        has_doubleheaders=False,
        allows_ties=True,
    )

    second = ProviderScheduleRules(
        regular_season_weeks=12,
        playoff_week_start=13,
        playoff_week_end=17,
        has_divisions=True,
        has_median_game=True,
        has_doubleheaders=False,
        allows_ties=False,
    )

    first_rule = repository.upsert_schedule_rule(
        context=context,
        schedule_rules=first,
    )
    database_session.flush()

    second_rule = repository.upsert_schedule_rule(
        context=context,
        schedule_rules=second,
    )
    database_session.flush()

    assert first_rule.id == second_rule.id
    assert schedule_rule_count(database_session) == 1

    database_session.refresh(second_rule)

    assert second_rule.playoff_week_end == 17
    assert second_rule.has_divisions is True
    assert second_rule.has_median_game is True
    assert second_rule.allows_ties is False
