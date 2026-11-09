from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    LineupSlotDefinition,
)
from app.db.session import engine
from app.domain.lineup import (
    DEFAULT_FOOTBALL_STARTING_SLOTS,
)
from app.repositories.player_week_repository import (
    load_team_week_rosters,
)


def test_roster_loader_uses_legacy_default_without_rules() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        session.execute(
            LineupSlotDefinition.__table__.delete().where(LineupSlotDefinition.season_id == 1)
        )
        session.flush()

        rosters = load_team_week_rosters(
            session,
            season_id=1,
            weeks=(1,),
        )

        assert rosters
        assert all(roster.starting_slots == DEFAULT_FOOTBALL_STARTING_SLOTS for roster in rosters)
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_roster_loader_repeats_dynamic_flex_count() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        session.execute(
            LineupSlotDefinition.__table__.delete().where(LineupSlotDefinition.season_id == 1)
        )

        rules = (
            ("QB", 0, 1),
            ("RB", 1, 2),
            ("WR", 2, 2),
            ("TE", 3, 1),
            ("FLEX", 4, 2),
            ("K", 5, 1),
            ("DST", 6, 1),
        )

        for index, (
            canonical_slot,
            slot_order,
            starter_count,
        ) in enumerate(rules):
            session.add(
                LineupSlotDefinition(
                    season_id=1,
                    provider_slot_code=("RB/WR/TE" if canonical_slot == "FLEX" else canonical_slot),
                    canonical_slot_code=(canonical_slot),
                    display_name=canonical_slot,
                    slot_order=slot_order,
                    starter_count=starter_count,
                    is_starter=True,
                    is_bench=False,
                    is_injured_reserve=False,
                    metadata_payload={
                        "test_rule": True,
                        "index": index,
                    },
                )
            )

        session.flush()

        rosters = load_team_week_rosters(
            session,
            season_id=1,
            weeks=(1,),
        )

        assert rosters

        expected = (
            "QB",
            "RB",
            "RB",
            "WR",
            "WR",
            "TE",
            "FLEX",
            "FLEX",
            "K",
            "DST",
        )

        assert all(roster.starting_slots == expected for roster in rosters)

        stored = session.scalars(
            select(LineupSlotDefinition)
            .where(LineupSlotDefinition.season_id == 1)
            .order_by(LineupSlotDefinition.slot_order)
        ).all()

        assert sum(definition.starter_count for definition in stored) == 10
    finally:
        session.close()
        transaction.rollback()
        connection.close()
