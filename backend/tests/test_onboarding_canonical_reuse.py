"""Onboarding initial sync reuses canonical Leagues and Seasons.

Exercises ``InitialWorkspaceSyncHandler._persist_identity`` against the
disposable test database. All data is synthetic; no provider is contacted.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.core.settings import get_settings
from app.db.auth_models import UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
    InstallationStatus,
    OnboardingStage,
    ProviderAccessMode,
    SeasonStatus,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation, ProviderConnection, ProviderLeagueClaim
from app.db.session import SessionFactory
from app.providers.domain import (
    ProviderCapabilities,
    ProviderLeague,
    ProviderScheduleRules,
    ProviderTeam,
)
from app.repositories.auth_repository import AuthenticationRepository
from app.services.background_job_worker import BackgroundJobExecutionError
from app.services.onboarding_initial_sync import InitialWorkspaceSyncHandler, _SyncContext

YEAR = 2024
MISMATCH = "workspace_ownership_mismatch"


@dataclass(frozen=True)
class Seeded:
    workspace_id: int
    connection_id: int
    league_id: str
    user_id: int


def _snapshot(league_id: str, name: str = "Synthetic Canonical League") -> ProviderLeague:
    return ProviderLeague(
        provider_league_id=league_id,
        provider_season_id=str(YEAR),
        name=name,
        sport=FantasySport.FOOTBALL,
        season_year=YEAR,
        current_week=1,
        regular_season_weeks=2,
        playoff_week_start=None,
        playoff_week_end=None,
        scoring_type="head_to_head_points",
        logo_url=None,
        teams=tuple(
            ProviderTeam(
                provider_team_id=f"team-{i}",
                name=f"Synthetic Team {i}",
                abbreviation=f"S{i}",
                owner_display_name=f"Synthetic Manager {i}",
                wins=0,
                losses=0,
                ties=0,
                points_for=Decimal("0.00"),
                points_against=Decimal("0.00"),
            )
            for i in (1, 2)
        ),
        lineup_slots=(),
        scoring_rules=(),
        schedule_rules=ProviderScheduleRules(
            regular_season_weeks=2,
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
            supports_lineup_history=False,
            supports_optimal_lineups=False,
            supports_schedule_swap=True,
            supports_all_play=True,
            supports_league_average=True,
            supports_team_logos=False,
        ),
    )


@pytest.fixture
def seed() -> Iterator[Callable[..., Seeded]]:
    workspaces: list[int] = []
    league_ids: list[str] = []

    def create(league_id: str | None = None) -> Seeded:
        suffix = uuid4().hex[:12]
        identity = league_id or str(100000 + uuid4().int % 800000)
        league_ids.append(identity)

        with SessionFactory() as session:
            workspace = Installation(
                application_name="Canonical Reuse League",
                slug=f"reuse-{suffix}",
                normalized_slug=f"reuse-{suffix}",
                timezone="America/Chicago",
                status=InstallationStatus.SETUP_IN_PROGRESS,
                registration_status=WorkspaceRegistrationStatus.PROVISIONAL,
                onboarding_stage=OnboardingStage.INITIAL_SYNC_RUNNING,
            )
            session.add(workspace)
            session.flush()
            workspaces.append(workspace.id)

            connection = ProviderConnection(
                installation_id=workspace.id,
                provider=FantasyProvider.ESPN,
                display_name="ESPN",
                is_active=True,
                access_mode=ProviderAccessMode.PUBLIC,
                provider_league_id=identity,
                provider_season_year=YEAR,
                last_validated_at=datetime.now(UTC),
            )
            session.add(connection)
            session.add(
                ProviderLeagueClaim(
                    workspace_id=workspace.id,
                    provider=FantasyProvider.ESPN,
                    provider_league_id=identity,
                    normalized_provider_league_id=identity,
                )
            )
            user = AuthenticationRepository(session).create_user(
                username=f"internal-{suffix}",
                display_name="Reuse Member",
                email=f"reuse-{suffix}@example.com",
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=workspace.id,
            )
            session.flush()
            result = Seeded(workspace.id, connection.id, identity, user.id)
            session.commit()

            return result

    yield create

    with SessionFactory() as session:
        # seasons.provider_connection_id is RESTRICT: delete leagues first.
        session.execute(
            text(
                "DELETE FROM leagues WHERE provider_league_id = ANY(:ids) "
                "OR installation_id = ANY(:ws)"
            ),
            {"ids": league_ids, "ws": workspaces},
        )
        session.execute(text("DELETE FROM installations WHERE id = ANY(:ws)"), {"ws": workspaces})
        session.commit()


def persist(seeded: Seeded) -> int:
    handler = InitialWorkspaceSyncHandler(session_factory=SessionFactory, settings=get_settings())
    context = _SyncContext(
        connection_id=seeded.connection_id,
        league_id=seeded.league_id,
        season_year=YEAR,
        swid=None,
        espn_s2=None,
        credential_marker="synthetic",
    )

    return handler._persist_identity(seeded.workspace_id, context, _snapshot(seeded.league_id))


def add_canonical(
    seeded: Seeded,
    *,
    installation_id: int | None = None,
    orphaned_at: datetime | None = None,
    connection_id: int | None = None,
    with_season: bool = True,
) -> tuple[int, int | None]:
    with SessionFactory() as session:
        league = League(
            provider=FantasyProvider.ESPN,
            provider_league_id=seeded.league_id,
            name="Synthetic Personal Import",
            installation_id=installation_id,
            orphaned_at=orphaned_at,
        )
        session.add(league)
        session.flush()
        season_id = None

        if with_season:
            season = Season(
                league_id=league.id,
                year=YEAR,
                current_week=1,
                regular_season_weeks=2,
                status=SeasonStatus.DRAFT,
                provider_connection_id=connection_id,
            )
            session.add(season)
            session.flush()
            season_id = season.id

        league_pk = league.id
        session.commit()

        return league_pk, season_id


def leagues_for(seeded: Seeded) -> list[League]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(League).where(
                    League.provider == FantasyProvider.ESPN,
                    League.provider_league_id == seeded.league_id,
                )
            )
        )
        session.expunge_all()

        return rows


def season_count(league_pk: int) -> int:
    with SessionFactory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Season)
                .where(Season.league_id == league_pk, Season.year == YEAR)
            )
            or 0
        )


def claim_league_id(workspace_id: int) -> int | None:
    with SessionFactory() as session:
        return session.scalar(
            select(ProviderLeagueClaim.league_id).where(
                ProviderLeagueClaim.workspace_id == workspace_id
            )
        )


def test_reuses_canonical_league_and_season_created_without_installation(seed) -> None:
    seeded = seed()
    league_pk, season_pk = add_canonical(seeded)

    returned = persist(seeded)

    leagues = leagues_for(seeded)
    assert [league.id for league in leagues] == [league_pk]
    assert leagues[0].installation_id == seeded.workspace_id
    assert returned == season_pk
    assert season_count(league_pk) == 1
    assert claim_league_id(seeded.workspace_id) == league_pk

    with SessionFactory() as session:
        season = session.get(Season, season_pk)
        assert season is not None
        assert season.provider_connection_id == seeded.connection_id


def test_reuse_clears_orphaned_marker(seed) -> None:
    seeded = seed()
    add_canonical(seeded, orphaned_at=datetime.now(UTC))

    persist(seeded)

    leagues = leagues_for(seeded)
    assert len(leagues) == 1
    assert leagues[0].orphaned_at is None


def test_league_owned_by_another_workspace_is_refused(seed) -> None:
    seeded = seed()
    other = seed()
    league_pk, _ = add_canonical(seeded, installation_id=other.workspace_id, with_season=False)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        persist(seeded)

    assert caught.value.safe_error_code == MISMATCH
    leagues = leagues_for(seeded)
    assert [league.id for league in leagues] == [league_pk]
    assert leagues[0].installation_id == other.workspace_id
    assert claim_league_id(seeded.workspace_id) is None


def test_season_connected_to_another_installation_is_refused(seed) -> None:
    seeded = seed()
    other = seed()
    league_pk, season_pk = add_canonical(seeded, connection_id=other.connection_id)

    with pytest.raises(BackgroundJobExecutionError) as caught:
        persist(seeded)

    assert caught.value.safe_error_code == MISMATCH
    assert season_count(league_pk) == 1

    with SessionFactory() as session:
        season = session.get(Season, season_pk)
        assert season is not None
        assert season.provider_connection_id == other.connection_id


def test_fresh_identity_creates_league_and_season_once(seed) -> None:
    seeded = seed()

    season_pk = persist(seeded)

    leagues = leagues_for(seeded)
    assert len(leagues) == 1
    assert leagues[0].installation_id == seeded.workspace_id
    assert season_count(leagues[0].id) == 1
    assert claim_league_id(seeded.workspace_id) == leagues[0].id

    with SessionFactory() as session:
        season = session.get(Season, season_pk)
        assert season is not None
        assert season.league_id == leagues[0].id
        assert season.provider_connection_id == seeded.connection_id


def test_existing_personal_access_row_is_untouched(seed) -> None:
    seeded = seed()
    _, season_pk = add_canonical(seeded)
    verified_at = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)

    with SessionFactory() as session:
        access = UserLeagueSeasonAccess(
            user_id=seeded.user_id,
            season_id=season_pk,
            proof_mode=UserSeasonProofMode.PUBLIC,
            provider_connection_id=None,
            status=UserSeasonAccessStatus.ACTIVE,
            verified_at=verified_at,
        )
        session.add(access)
        session.commit()
        access_id = access.id

    persist(seeded)

    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.season_id == season_pk)
            )
        )
        assert [row.id for row in rows] == [access_id]
        row = rows[0]
        assert row.user_id == seeded.user_id
        assert row.status is UserSeasonAccessStatus.ACTIVE
        assert row.proof_mode is UserSeasonProofMode.PUBLIC
        assert row.provider_connection_id is None
        assert row.verified_at == verified_at
