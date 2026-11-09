from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import (
    LineupSlotDefinition,
    LineupSlotEligibility,
    ScheduleRule,
)
from app.db.platform_enums import FantasySport
from app.db.session import engine
from app.providers.domain import (
    ProviderCapabilities,
    ProviderLeague,
    ProviderLineupSlot,
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
        lineup_slots=(
            ProviderLineupSlot(
                provider_slot_code="QB",
                canonical_slot_code="QB",
                display_name="QB",
                order=0,
                starter_count=1,
                is_starter=True,
                is_bench=False,
                is_injured_reserve=False,
                eligible_positions=("QB",),
            ),
            ProviderLineupSlot(
                provider_slot_code="RB",
                canonical_slot_code="RB",
                display_name="RB",
                order=1,
                starter_count=2,
                is_starter=True,
                is_bench=False,
                is_injured_reserve=False,
                eligible_positions=("RB",),
            ),
            ProviderLineupSlot(
                provider_slot_code="WR",
                canonical_slot_code="WR",
                display_name="WR",
                order=2,
                starter_count=2,
                is_starter=True,
                is_bench=False,
                is_injured_reserve=False,
                eligible_positions=("WR",),
            ),
            ProviderLineupSlot(
                provider_slot_code="TE",
                canonical_slot_code="TE",
                display_name="TE",
                order=3,
                starter_count=1,
                is_starter=True,
                is_bench=False,
                is_injured_reserve=False,
                eligible_positions=("TE",),
            ),
            ProviderLineupSlot(
                provider_slot_code="RB/WR/TE",
                canonical_slot_code="FLEX",
                display_name="FLEX",
                order=4,
                starter_count=1,
                is_starter=True,
                is_bench=False,
                is_injured_reserve=False,
                eligible_positions=(
                    "RB",
                    "WR",
                    "TE",
                ),
            ),
            ProviderLineupSlot(
                provider_slot_code="K",
                canonical_slot_code="K",
                display_name="K",
                order=5,
                starter_count=1,
                is_starter=True,
                is_bench=False,
                is_injured_reserve=False,
                eligible_positions=("K",),
            ),
            ProviderLineupSlot(
                provider_slot_code="D/ST",
                canonical_slot_code="DST",
                display_name="DST",
                order=6,
                starter_count=1,
                is_starter=True,
                is_bench=False,
                is_injured_reserve=False,
                eligible_positions=("DST",),
            ),
            ProviderLineupSlot(
                provider_slot_code="BE",
                canonical_slot_code="BENCH",
                display_name="BENCH",
                order=7,
                starter_count=0,
                is_starter=False,
                is_bench=True,
                is_injured_reserve=False,
                eligible_positions=(),
            ),
            ProviderLineupSlot(
                provider_slot_code="IR",
                canonical_slot_code="IR",
                display_name="IR",
                order=8,
                starter_count=0,
                is_starter=False,
                is_bench=False,
                is_injured_reserve=True,
                eligible_positions=(),
            ),
        ),
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

    lineup_definitions = database_session.scalars(
        select(LineupSlotDefinition)
        .where(
            LineupSlotDefinition.season_id == context.season_id,
        )
        .order_by(
            LineupSlotDefinition.slot_order,
        )
    ).all()

    assert [
        (
            definition.provider_slot_code,
            definition.canonical_slot_code,
            definition.starter_count,
            definition.is_starter,
        )
        for definition in lineup_definitions
    ] == [
        ("QB", "QB", 1, True),
        ("RB", "RB", 2, True),
        ("WR", "WR", 2, True),
        ("TE", "TE", 1, True),
        ("RB/WR/TE", "FLEX", 1, True),
        ("K", "K", 1, True),
        ("D/ST", "DST", 1, True),
        ("BE", "BENCH", 0, False),
        ("IR", "IR", 0, False),
    ]

    flex_definition = next(
        definition for definition in lineup_definitions if definition.canonical_slot_code == "FLEX"
    )

    flex_eligibility = database_session.scalars(
        select(LineupSlotEligibility)
        .where(
            LineupSlotEligibility.lineup_slot_definition_id == flex_definition.id,
        )
        .order_by(
            LineupSlotEligibility.canonical_position,
        )
    ).all()

    assert [eligibility.canonical_position for eligibility in flex_eligibility] == [
        "RB",
        "TE",
        "WR",
    ]

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


def test_lineup_rule_replacement_preserves_double_flex(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )
    provider_league = make_provider_league()

    double_flex_slots = tuple(
        ProviderLineupSlot(
            provider_slot_code=slot.provider_slot_code,
            canonical_slot_code=slot.canonical_slot_code,
            display_name=slot.display_name,
            order=slot.order,
            starter_count=(2 if slot.canonical_slot_code == "FLEX" else slot.starter_count),
            is_starter=slot.is_starter,
            is_bench=slot.is_bench,
            is_injured_reserve=(slot.is_injured_reserve),
            eligible_positions=(slot.eligible_positions),
            metadata=slot.metadata,
        )
        for slot in provider_league.lineup_slots
    )

    stored = repository.replace_lineup_slot_definitions(
        context=context,
        lineup_slots=double_flex_slots,
    )

    database_session.flush()

    flex = next(definition for definition in stored if definition.canonical_slot_code == "FLEX")

    assert flex.starter_count == 2

    total_starters = sum(definition.starter_count for definition in stored if definition.is_starter)

    assert total_starters == 10


def test_lineup_rule_replacement_validates_before_delete(
    database_session: Session,
) -> None:
    repository = ProviderSyncRepository(database_session)
    context = repository.load_context(
        season_id=1,
    )

    valid_slots = (
        ProviderLineupSlot(
            provider_slot_code="QB",
            canonical_slot_code="QB",
            display_name="QB",
            order=0,
            starter_count=1,
            is_starter=True,
            is_bench=False,
            is_injured_reserve=False,
            eligible_positions=("QB",),
        ),
    )

    repository.replace_lineup_slot_definitions(
        context=context,
        lineup_slots=valid_slots,
    )
    database_session.flush()

    duplicate_slots = (
        valid_slots[0],
        ProviderLineupSlot(
            provider_slot_code="QB",
            canonical_slot_code="SUPERFLEX",
            display_name="SUPERFLEX",
            order=1,
            starter_count=1,
            is_starter=True,
            is_bench=False,
            is_injured_reserve=False,
            eligible_positions=(
                "QB",
                "RB",
                "WR",
                "TE",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="duplicate provider slot",
    ):
        repository.replace_lineup_slot_definitions(
            context=context,
            lineup_slots=duplicate_slots,
        )

    remaining = database_session.scalars(
        select(LineupSlotDefinition).where(
            LineupSlotDefinition.season_id == context.season_id,
        )
    ).all()

    assert len(remaining) == 1
    assert remaining[0].provider_slot_code == "QB"
