"""Committed full-tenant-graph fixtures for FR-20 final deletion tests.

Rows are created through separate committed ``SessionFactory`` sessions
so race tests can use independent PostgreSQL connections. Bulk domain
rows are generated with set-based SQL scoped to the fixture's own IDs.
All credentials are synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.db.auth_models import (
    AuthSession,
    AuthSessionPurpose,
    EmailVerificationToken,
    PasswordResetToken,
    User,
    UserRole,
)
from app.db.models import (
    Franchise,
    League,
    LineupSlotDefinition,
    LineupSlotEligibility,
    Matchup,
    OptimalLineupResult,
    PlayerWeekScore,
    ScheduleRule,
    ScoringRule,
    Season,
    SyncRun,
    SyncStatus,
    Team,
)
from app.db.platform_enums import (
    BackgroundJobType,
    FantasyProvider,
    FantasySport,
    InstallationStatus,
    OnboardingStage,
    SeasonStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
    WorkspaceDeletionRecord,
)
from app.db.session import SessionFactory
from app.providers.credential_vault import ProviderCredentialVault
from app.providers.espn import credentials_from_values
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.workspace_deletion_repository import WorkspaceDeletionRepository
from app.services.workspace_deletion import workspace_deletion_idempotency_key

ADMIN_PASSWORD = "correct horse battery staple"
SPECTATOR_PASSWORD = "shared spectator password 2026"
CREDENTIAL_KEY_VERSION = 1
# Claimed ahead of any leftover queued job in the shared test database.
TEST_JOB_PRIORITY = 10_000


@dataclass(slots=True)
class DeletionGraph:
    workspace_id: int
    slug: str
    administrator_id: int
    spectator_id: int
    administrator_email: str
    administrator_username: str
    spectator_username: str
    display_name: str
    connection_id: int
    connection_display_name: str
    ciphertext: str
    claim_id: int
    league_id: int
    provider_league_id: str
    season_ids: list[int]
    requested_at: datetime
    scheduled_for: datetime
    sync_run_ids: list[int] = field(default_factory=list)


def create_deletion_graph(
    *,
    seasons: int = 2,
    teams: int = 4,
    weeks: int = 3,
    players_per_team: int = 3,
    pending: bool = True,
    requested_at: datetime | None = None,
) -> DeletionGraph:
    """Create one synchronized workspace with every owned table populated."""

    suffix = uuid4().hex[:12]
    slug = f"final-deletion-{suffix}"
    email = f"final-deletion-{suffix}@example.com"
    provider_league_id = f"final-deletion-league-{suffix}"
    request_time = requested_at or (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0)
    scheduled_for = request_time + timedelta(days=30)
    vault = ProviderCredentialVault(
        keys={CREDENTIAL_KEY_VERSION: Fernet.generate_key().decode("ascii")}
    )

    with SessionFactory() as session:
        workspace = Installation(
            application_name=f"Final Deletion League {suffix}",
            slug=slug,
            normalized_slug=slug,
            timezone="America/Chicago",
            status=InstallationStatus.ACTIVE,
            registration_status=(
                WorkspaceRegistrationStatus.DELETION_PENDING
                if pending
                else WorkspaceRegistrationStatus.ACTIVE
            ),
            onboarding_stage=OnboardingStage.COMPLETE,
            deletion_requested_at=request_time if pending else None,
            deletion_scheduled_for=scheduled_for if pending else None,
        )
        session.add(workspace)
        session.flush()

        auth = AuthenticationRepository(session)
        administrator = auth.create_user(
            username=f"fd-admin-{suffix}",
            display_name=f"Final Deletion Admin {suffix}",
            email=email,
            password=ADMIN_PASSWORD,
            role=UserRole.ADMIN,
            workspace_id=workspace.id,
        )
        administrator.email_verified_at = request_time - timedelta(days=10)
        spectator = auth.create_user(
            username=f"fd-spectator-{suffix}",
            display_name=f"Final Deletion Spectator {suffix}",
            password=SPECTATOR_PASSWORD,
            role=UserRole.SPECTATOR,
            workspace_id=workspace.id,
        )
        session.flush()

        sessions = SessionRepository(session)
        sessions.create_session(user=administrator, lifetime_hours=8)
        sessions.create_session(
            user=administrator,
            lifetime=timedelta(minutes=15),
            purpose=AuthSessionPurpose.DELETION_RECOVERY,
        )
        sessions.create_session(user=spectator, lifetime_hours=8)
        token_expiry = datetime.now(UTC) + timedelta(hours=1)
        session.add_all(
            [
                EmailVerificationToken(
                    user_id=administrator.id,
                    token_hash=f"verify-{suffix}",
                    expires_at=token_expiry,
                ),
                PasswordResetToken(
                    user_id=administrator.id,
                    token_hash=f"reset-{suffix}",
                    expires_at=token_expiry,
                ),
            ]
        )

        connection_display_name = f"Final Deletion Connection {suffix}"
        ciphertext = vault.encrypt(
            provider=FantasyProvider.ESPN,
            credentials=credentials_from_values(
                league_id=provider_league_id,
                season="2026",
                espn_s2=f"synthetic-cookie-{suffix}",
                swid=f"{{synthetic-swid-{suffix}}}",
            ),
            key_version=CREDENTIAL_KEY_VERSION,
        )
        connection = ProviderConnection(
            installation_id=workspace.id,
            provider=FantasyProvider.ESPN,
            display_name=connection_display_name,
            encrypted_credentials=ciphertext,
            credential_version=CREDENTIAL_KEY_VERSION,
            is_active=True,
        )
        session.add(connection)
        session.flush()

        league = League(
            name=f"Final Deletion League {suffix}",
            installation_id=workspace.id,
            provider=FantasyProvider.ESPN,
            provider_league_id=provider_league_id,
            sport=FantasySport.FOOTBALL,
        )
        session.add(league)
        session.flush()

        claim = ProviderLeagueClaim(
            workspace_id=workspace.id,
            league_id=league.id,
            provider=FantasyProvider.ESPN,
            provider_league_id=provider_league_id,
            normalized_provider_league_id=provider_league_id,
        )
        session.add(claim)

        season_rows = [
            Season(
                league_id=league.id,
                year=2026 - index,
                current_week=weeks,
                regular_season_weeks=weeks,
                provider_connection_id=connection.id,
                provider_season_id=str(2026 - index),
                status=SeasonStatus.ACTIVE if index == 0 else SeasonStatus.COMPLETED,
            )
            for index in range(seasons)
        ]
        session.add_all(season_rows)
        session.flush()
        season_ids = [season.id for season in season_rows]

        workspace.active_league_id = league.id
        workspace.active_season_id = season_ids[0]
        session.add(Franchise(league_id=league.id, display_name=f"Franchise {suffix}"))
        session.flush()

        _populate_season_data(
            session,
            season_ids=season_ids,
            teams=teams,
            weeks=weeks,
            players_per_team=players_per_team,
        )

        season_run = SyncRun(
            season_id=season_ids[0],
            provider_connection_id=connection.id,
            provider=FantasyProvider.ESPN,
            status=SyncStatus.SUCCEEDED,
            is_full_sync=True,
            teams_upserted=teams,
            matchups_upserted=0,
            player_week_scores_upserted=0,
            analytics_rebuilt=True,
        )
        # A nullable-season run that no Season cascade would remove.
        connection_only_run = SyncRun(
            season_id=None,
            provider_connection_id=connection.id,
            provider=FantasyProvider.ESPN,
            status=SyncStatus.FAILED,
            is_full_sync=False,
            teams_upserted=0,
            matchups_upserted=0,
            player_week_scores_upserted=0,
            analytics_rebuilt=False,
        )
        session.add_all([season_run, connection_only_run])
        session.flush()

        session.commit()

        return DeletionGraph(
            workspace_id=workspace.id,
            slug=slug,
            administrator_id=administrator.id,
            spectator_id=spectator.id,
            administrator_email=email,
            administrator_username=administrator.username,
            spectator_username=spectator.username,
            display_name=administrator.display_name,
            connection_id=connection.id,
            connection_display_name=connection_display_name,
            ciphertext=ciphertext,
            claim_id=claim.id,
            league_id=league.id,
            provider_league_id=provider_league_id,
            season_ids=season_ids,
            requested_at=request_time,
            scheduled_for=scheduled_for,
            sync_run_ids=[season_run.id, connection_only_run.id],
        )


def _populate_season_data(
    session: Session,
    *,
    season_ids: list[int],
    teams: int,
    weeks: int,
    players_per_team: int,
) -> None:
    for season_id in season_ids:
        session.add(ScheduleRule(season_id=season_id, regular_season_weeks=weeks))
        session.add(
            ScoringRule(
                season_id=season_id,
                provider_stat_code="53",
                display_name="Receptions",
                points_per_unit=1,
            )
        )
        slot = LineupSlotDefinition(
            season_id=season_id,
            provider_slot_code="2",
            canonical_slot_code="RB",
            display_name="Running Back",
            slot_order=0,
        )
        session.add(slot)
        session.flush()
        session.add(
            LineupSlotEligibility(lineup_slot_definition_id=slot.id, canonical_position="RB")
        )

    session.flush()
    params = {
        "season_ids": season_ids,
        "teams": teams,
        "weeks": weeks,
        "players": players_per_team,
    }
    session.execute(
        text(
            """
            INSERT INTO teams (season_id, provider_team_id, name, wins, losses, ties,
                               points_for, points_against, created_at, updated_at)
            SELECT s.id, t::text, 'Team ' || t, 0, 0, 0, 0, 0, now(), now()
            FROM unnest(CAST(:season_ids AS integer[])) AS s(id)
            CROSS JOIN generate_series(1, :teams) AS t
            """
        ),
        params,
    )
    session.execute(
        text(
            """
            INSERT INTO matchups (season_id, week, home_team_id, away_team_id,
                                  is_complete, is_bye, created_at, updated_at)
            SELECT home.season_id, w, home.id, away.id, true, false, now(), now()
            FROM teams home
            JOIN teams away
              ON away.season_id = home.season_id
             AND away.provider_team_id::integer = home.provider_team_id::integer + 1
            CROSS JOIN generate_series(1, :weeks) AS w
            WHERE home.season_id = ANY(CAST(:season_ids AS integer[]))
              AND home.provider_team_id::integer % 2 = 1
            """
        ),
        params,
    )
    session.execute(
        text(
            """
            INSERT INTO player_week_scores (
                season_id, team_id, week, provider_player_id, player_name,
                default_position, actual_lineup_slot, eligible_lineup_slots, points,
                was_on_roster, is_starter, is_bench, is_injured_reserve,
                created_at, updated_at)
            SELECT team.season_id, team.id, w, p::text, 'Player ' || p,
                   'RB', 'RB', '["RB"]'::json, 10.5,
                   true, p <= 2, p > 2, false, now(), now()
            FROM teams team
            CROSS JOIN generate_series(1, :weeks) AS w
            CROSS JOIN generate_series(1, :players) AS p
            WHERE team.season_id = ANY(CAST(:season_ids AS integer[]))
            """
        ),
        params,
    )
    session.execute(
        text(
            """
            INSERT INTO optimal_lineup_results (
                season_id, team_id, week, team_name, actual_points, optimal_points,
                missed_points, efficiency_percentage, actual_starter_count,
                optimal_filled_slot_count, unfilled_slots, actual_lineup,
                optimal_lineup, substitutions, calculation_version, calculated_at)
            SELECT team.season_id, team.id, w, team.name, 100, 110, 10, 90.91, 9, 9,
                   '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 1, now()
            FROM teams team
            CROSS JOIN generate_series(1, :weeks) AS w
            WHERE team.season_id = ANY(CAST(:season_ids AS integer[]))
            """
        ),
        params,
    )


def enqueue_deletion_job(
    graph: DeletionGraph,
    *,
    maximum_attempts: int = 5,
) -> int:
    """Enqueue the graph's deletion job at its scheduled time."""

    with SessionFactory() as session:
        result = BackgroundJobRepository(session).enqueue(
            workspace_id=graph.workspace_id,
            job_type=BackgroundJobType.WORKSPACE_DELETION,
            payload={},
            idempotency_key=workspace_deletion_idempotency_key(graph.workspace_id),
            available_at=graph.scheduled_for,
            priority=TEST_JOB_PRIORITY,
            maximum_attempts=maximum_attempts,
        )
        session.commit()

        return result.job.id


def owned_row_counts(graph: DeletionGraph) -> dict[str, int]:
    """Count every row owned by the graph's workspace, by table."""

    user_ids = [graph.administrator_id, graph.spectator_id]
    seasons = graph.season_ids
    workspace_id = graph.workspace_id

    def count(statement) -> int:
        return int(session.scalar(statement) or 0)

    with SessionFactory() as session:
        slot_ids = select(LineupSlotDefinition.id).where(
            LineupSlotDefinition.season_id.in_(seasons)
        )

        return {
            "installations": count(select(func.count()).where(Installation.id == workspace_id)),
            "users": count(select(func.count()).where(User.workspace_id == workspace_id)),
            "auth_sessions": count(select(func.count()).where(AuthSession.user_id.in_(user_ids))),
            "email_verification_tokens": count(
                select(func.count()).where(EmailVerificationToken.user_id.in_(user_ids))
            ),
            "password_reset_tokens": count(
                select(func.count()).where(PasswordResetToken.user_id.in_(user_ids))
            ),
            "provider_connections": count(
                select(func.count()).where(ProviderConnection.installation_id == workspace_id)
            ),
            "provider_league_claims": count(
                select(func.count()).where(ProviderLeagueClaim.workspace_id == workspace_id)
            ),
            "leagues": count(select(func.count()).where(League.installation_id == workspace_id)),
            "franchises": count(select(func.count()).where(Franchise.league_id == graph.league_id)),
            "seasons": count(select(func.count()).where(Season.id.in_(seasons))),
            "teams": count(select(func.count()).where(Team.season_id.in_(seasons))),
            "matchups": count(select(func.count()).where(Matchup.season_id.in_(seasons))),
            "player_week_scores": count(
                select(func.count()).where(PlayerWeekScore.season_id.in_(seasons))
            ),
            "optimal_lineup_results": count(
                select(func.count()).where(OptimalLineupResult.season_id.in_(seasons))
            ),
            "lineup_slot_definitions": count(
                select(func.count()).where(LineupSlotDefinition.season_id.in_(seasons))
            ),
            "lineup_slot_eligibility": count(
                select(func.count()).where(
                    LineupSlotEligibility.lineup_slot_definition_id.in_(slot_ids)
                )
            ),
            "scoring_rules": count(select(func.count()).where(ScoringRule.season_id.in_(seasons))),
            "schedule_rules": count(
                select(func.count()).where(ScheduleRule.season_id.in_(seasons))
            ),
            "sync_runs": count(select(func.count()).where(SyncRun.id.in_(graph.sync_run_ids))),
            "background_jobs": count(
                select(func.count()).where(BackgroundJob.workspace_id == workspace_id)
            ),
        }


def deletion_records(workspace_id: int) -> list[WorkspaceDeletionRecord]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(WorkspaceDeletionRecord).where(
                    WorkspaceDeletionRecord.former_workspace_id == workspace_id
                )
            ).all()
        )
        for row in rows:
            session.expunge(row)

        return rows


def load_workspace(workspace_id: int) -> Installation | None:
    with SessionFactory() as session:
        workspace = session.get(Installation, workspace_id)
        if workspace is not None:
            session.expunge(workspace)

        return workspace


def load_job(job_id: int) -> BackgroundJob | None:
    with SessionFactory() as session:
        job = session.get(BackgroundJob, job_id)
        if job is not None:
            session.expunge(job)

        return job


def cleanup_graphs(workspace_ids: list[int]) -> None:
    """Remove leftover fixture workspaces and their deletion records."""

    with SessionFactory() as session:
        repository = WorkspaceDeletionRepository(session)
        for workspace_id in workspace_ids:
            repository.delete_connection_sync_runs(workspace_id)
            repository.delete_leagues(workspace_id)
            repository.delete_workspace(workspace_id)
        if workspace_ids:
            session.execute(
                delete(WorkspaceDeletionRecord).where(
                    WorkspaceDeletionRecord.former_workspace_id.in_(workspace_ids)
                )
            )
        session.commit()
