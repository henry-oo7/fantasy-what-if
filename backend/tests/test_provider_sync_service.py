from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import model_registry  # noqa: F401
from app.db.models import (
    Matchup,
    OptimalLineupResult,
    PlayerWeekScore,
    SyncRun,
    SyncStatus,
    Team,
)
from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
)
from app.db.session import engine
from app.providers.domain import (
    ProviderCapabilities,
    ProviderLeague,
    ProviderMatchup,
    ProviderPlayerWeek,
    ProviderScheduleRules,
    ProviderTeam,
    ProviderValidationResult,
    ProviderWeeklyRoster,
)
from app.services.provider_sync import (
    synchronize_provider,
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

        if transaction.is_active:
            transaction.rollback()

        connection.close()


def make_team(
    provider_team_id: str,
) -> ProviderTeam:
    return ProviderTeam(
        provider_team_id=provider_team_id,
        name=f"Canonical Team {provider_team_id}",
        wins=5,
        losses=3,
        ties=0,
        points_for=Decimal("1000.00"),
        points_against=Decimal("950.00"),
    )


def make_player(
    provider_player_id: str,
) -> ProviderPlayerWeek:
    return ProviderPlayerWeek(
        provider_player_id=provider_player_id,
        player_name=f"Player {provider_player_id}",
        pro_team="HOU",
        provider_position="WR",
        canonical_position="WR",
        actual_lineup_slot="WR",
        eligible_lineup_slots=(
            "WR",
            "RB/WR/TE",
            "BE",
        ),
        points=Decimal("15.50"),
        projected_points=Decimal("14.25"),
        is_starter=True,
        is_bench=False,
        is_injured_reserve=False,
        is_on_roster=True,
        metadata={
            "provider": "espn",
        },
    )


class FakeProvider:
    provider = FantasyProvider.ESPN

    def __init__(self) -> None:
        self.fetch_league_calls = 0
        self.matchup_requests: list[tuple[int, ...]] = []
        self.roster_requests: list[tuple[int, ...]] = []

    def validate_configuration(
        self,
    ) -> ProviderValidationResult:
        return ProviderValidationResult(
            is_valid=True,
            provider=self.provider,
            safe_message="Configuration is valid.",
        )

    def discover_leagues(self) -> tuple:
        return ()

    def fetch_league(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
    ) -> ProviderLeague:
        self.fetch_league_calls += 1

        assert provider_league_id == "529250970"
        assert provider_season_id == "2026"

        teams = tuple(make_team(str(team_id)) for team_id in range(1, 13))

        return ProviderLeague(
            provider_league_id="529250970",
            provider_season_id="2026",
            name="Jimmy and Friends",
            sport=FantasySport.FOOTBALL,
            season_year=2026,
            current_week=9,
            regular_season_weeks=12,
            playoff_week_start=None,
            playoff_week_end=None,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=teams,
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=ProviderScheduleRules(
                regular_season_weeks=12,
                playoff_week_start=None,
                playoff_week_end=None,
                has_divisions=False,
                has_median_game=False,
                has_doubleheaders=False,
                allows_ties=True,
            ),
            capabilities=ProviderCapabilities(
                supports_head_to_head=True,
                supports_weekly_matchups=True,
                supports_lineup_history=True,
                supports_optimal_lineups=False,
                supports_schedule_swap=True,
                supports_all_play=True,
                supports_league_average=True,
                supports_team_logos=False,
            ),
        )

    def fetch_matchups(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderMatchup, ...]:
        assert provider_league_id == "529250970"
        assert provider_season_id == "2026"

        self.matchup_requests.append(weeks)

        matchups: list[ProviderMatchup] = []

        for week in weeks:
            for home_team_id in range(
                1,
                13,
                2,
            ):
                matchups.append(
                    ProviderMatchup(
                        week=week,
                        home_provider_team_id=str(home_team_id),
                        away_provider_team_id=str(home_team_id + 1),
                        home_score=(Decimal("100.00") if week < 9 else None),
                        away_score=(Decimal("90.00") if week < 9 else None),
                        is_complete=week < 9,
                        is_bye=False,
                    )
                )

        return tuple(matchups)

    def fetch_weekly_rosters(
        self,
        *,
        provider_league_id: str,
        provider_season_id: str,
        weeks: tuple[int, ...],
    ) -> tuple[ProviderWeeklyRoster, ...]:
        assert provider_league_id == "529250970"
        assert provider_season_id == "2026"

        self.roster_requests.append(weeks)

        return tuple(
            ProviderWeeklyRoster(
                provider_team_id=str(team_id),
                week=week,
                players=(make_player(f"{week}{team_id:02d}001"),),
            )
            for week in weeks
            for team_id in range(1, 13)
        )


class WrongProvider(FakeProvider):
    provider = FantasyProvider.SLEEPER


def count_rows(
    session: Session,
    model: type[Team | Matchup | PlayerWeekScore | OptimalLineupResult | SyncRun],
) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_incremental_provider_sync_orchestrates_canonical_writes(
    database_session: Session,
) -> None:
    provider = FakeProvider()

    result = synchronize_provider(
        session=database_session,
        season_id=1,
        provider=provider,
        full_sync=False,
        explicit_from_week=8,
        explicit_through_week=8,
    )

    assert result.season_id == 1
    assert result.provider_league_id == "529250970"
    assert result.provider_season_id == "2026"
    assert result.current_week == 9
    assert result.full_sync is False
    assert result.matchup_weeks == (8,)
    assert result.player_weeks == (8,)
    assert result.teams_upserted == 12
    assert result.matchups_upserted == 6
    assert result.fantasy_byes_upserted == 0
    assert result.player_week_scores_upserted == 12
    assert result.analytics_rebuilt is True

    assert provider.fetch_league_calls == 1
    assert provider.matchup_requests == [
        (8,),
    ]
    assert provider.roster_requests == [
        (8,),
    ]

    assert (
        count_rows(
            database_session,
            Team,
        )
        == 12
    )
    assert (
        count_rows(
            database_session,
            Matchup,
        )
        == 72
    )

    week_eight_players = database_session.scalar(
        select(func.count())
        .select_from(PlayerWeekScore)
        .where(
            PlayerWeekScore.season_id == 1,
            PlayerWeekScore.week == 8,
        )
    )

    assert week_eight_players == 12

    latest_run = database_session.scalar(
        select(SyncRun)
        .where(SyncRun.season_id == 1)
        .order_by(
            SyncRun.id.desc(),
        )
        .limit(1)
    )

    assert latest_run is not None
    assert latest_run.status is SyncStatus.SUCCEEDED
    assert latest_run.provider is FantasyProvider.ESPN
    assert latest_run.provider_connection_id == 1
    assert latest_run.requested_from_week == 8
    assert latest_run.requested_through_week == 8
    assert latest_run.teams_upserted == 12
    assert latest_run.matchups_upserted == 6
    assert latest_run.player_week_scores_upserted == 12
    assert latest_run.analytics_rebuilt is True

    week_eight_optimal_results = database_session.scalar(
        select(func.count())
        .select_from(OptimalLineupResult)
        .where(
            OptimalLineupResult.season_id == 1,
            OptimalLineupResult.week == 8,
        )
    )

    assert week_eight_optimal_results == 12


def test_provider_must_match_season_connection(
    database_session: Session,
) -> None:
    with pytest.raises(
        ValueError,
        match="does not match",
    ):
        synchronize_provider(
            session=database_session,
            season_id=1,
            provider=WrongProvider(),
            full_sync=False,
            explicit_from_week=8,
            explicit_through_week=8,
        )


def test_invalid_provider_data_rolls_back(
    database_session: Session,
) -> None:
    before_team_count = count_rows(
        database_session,
        Team,
    )
    before_matchup_count = count_rows(
        database_session,
        Matchup,
    )
    before_player_count = count_rows(
        database_session,
        PlayerWeekScore,
    )

    class InvalidProvider(FakeProvider):
        def fetch_matchups(
            self,
            *,
            provider_league_id: str,
            provider_season_id: str,
            weeks: tuple[int, ...],
        ) -> tuple[ProviderMatchup, ...]:
            del provider_league_id
            del provider_season_id

            return (
                ProviderMatchup(
                    week=weeks[0],
                    home_provider_team_id="1",
                    away_provider_team_id="unknown",
                    home_score=Decimal("100"),
                    away_score=Decimal("90"),
                    is_complete=True,
                    is_bye=False,
                ),
            )

    with pytest.raises(ValueError):
        synchronize_provider(
            session=database_session,
            season_id=1,
            provider=InvalidProvider(),
            full_sync=False,
            explicit_from_week=8,
            explicit_through_week=8,
        )

    assert (
        count_rows(
            database_session,
            Team,
        )
        == before_team_count
    )
    assert (
        count_rows(
            database_session,
            Matchup,
        )
        == before_matchup_count
    )
    assert (
        count_rows(
            database_session,
            PlayerWeekScore,
        )
        == before_player_count
    )


def test_provider_sync_can_leave_transaction_to_caller(
    database_session: Session,
) -> None:
    provider = FakeProvider()

    result = synchronize_provider(
        session=database_session,
        season_id=1,
        provider=provider,
        full_sync=False,
        explicit_from_week=8,
        explicit_through_week=8,
        commit_transaction=False,
    )

    assert result.season_id == 1
    assert result.teams_upserted == 12
    assert result.matchups_upserted == 6
    assert result.player_week_scores_upserted == 12

    assert database_session.in_transaction() is True

    latest_run = database_session.scalar(
        select(SyncRun).where(SyncRun.season_id == 1).order_by(SyncRun.id.desc()).limit(1)
    )

    assert latest_run is not None
    assert latest_run.status is SyncStatus.SUCCEEDED
