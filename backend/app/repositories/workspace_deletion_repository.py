"""Workspace-scoped persistence for FR-20 final workspace deletion.

Every statement here is bounded by one workspace ID. The ordered deletes
rely on the existing ``ON DELETE CASCADE`` rules for imported domain data
and authentication rows; nothing disables or bypasses a constraint.

``seasons.provider_connection_id`` is ``RESTRICT``, so League (and
therefore Season) rows must be deleted before the workspace row cascades
its provider connections. ``sync_runs.provider_connection_id`` is
``SET NULL``, so connection-linked runs that would not disappear with a
Season are deleted explicitly instead of being orphaned.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db.auth_models import User, UserRole
from app.db.models import League, Season, SyncRun
from app.db.platform_enums import FantasyProvider, WorkspaceDeletionOutcome
from app.db.platform_models import (
    BackgroundJob,
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
    WorkspaceDeletionRecord,
)


class WorkspaceDeletionRepository:
    """Lock, capture, and delete one workspace's tenant graph."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_workspace(self, workspace_id: int) -> Installation | None:
        return self.session.scalar(
            select(Installation)
            .where(Installation.id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def lock_job(self, job_id: int) -> BackgroundJob | None:
        """Lock the running deletion job after the workspace row.

        Holding this lock for the destructive transaction keeps expired-lease
        recovery (``SKIP LOCKED``) from requeueing the job mid-deletion.
        """

        return self.session.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def lock_administrators(self, workspace_id: int) -> list[User]:
        """Lock the workspace's administrator rows after the workspace row.

        The initial release has exactly one; the caller enforces that.
        """

        return list(
            self.session.scalars(
                select(User)
                .where(User.workspace_id == workspace_id, User.role == UserRole.ADMIN)
                .order_by(User.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def get_claim(self, workspace_id: int) -> ProviderLeagueClaim | None:
        return self.session.scalar(
            select(ProviderLeagueClaim).where(ProviderLeagueClaim.workspace_id == workspace_id)
        )

    def completed_record_exists(self, workspace_id: int) -> bool:
        return bool(
            self.session.scalar(
                select(
                    exists().where(
                        WorkspaceDeletionRecord.former_workspace_id == workspace_id,
                        WorkspaceDeletionRecord.outcome == WorkspaceDeletionOutcome.COMPLETED,
                    )
                )
            )
        )

    def delete_connection_sync_runs(self, workspace_id: int) -> int:
        """Delete runs tied to this workspace's connections.

        Covers nullable-season runs, which no Season cascade would remove
        and which ``SET NULL`` would otherwise orphan. Runs whose Season
        belongs to this workspace are included; runs linked to another
        workspace's Season are never touched.
        """

        connection_ids = self._connection_ids(workspace_id)
        workspace_season_ids = (
            select(Season.id)
            .join(League, League.id == Season.league_id)
            .where(League.installation_id == workspace_id)
        )
        result = self.session.execute(
            delete(SyncRun)
            .where(
                SyncRun.provider_connection_id.in_(connection_ids),
                or_(
                    SyncRun.season_id.is_(None),
                    SyncRun.season_id.in_(workspace_season_ids),
                ),
            )
            .execution_options(synchronize_session=False)
        )

        return int(result.rowcount or 0)

    def delete_leagues(self, workspace_id: int) -> int:
        """Delete workspace leagues; seasons and domain data cascade."""

        result = self.session.execute(
            delete(League)
            .where(League.installation_id == workspace_id)
            .execution_options(synchronize_session=False)
        )

        return int(result.rowcount or 0)

    def count_seasons_referencing_connections(self, workspace_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(Season)
                .where(Season.provider_connection_id.in_(self._connection_ids(workspace_id)))
            )
            or 0
        )

    def add_completed_record(
        self,
        *,
        former_workspace_id: int,
        provider: FantasyProvider,
        normalized_provider_league_id: str,
        requested_at: datetime,
        scheduled_for: datetime,
        completed_at: datetime,
        retention_expires_at: datetime,
    ) -> WorkspaceDeletionRecord:
        record = WorkspaceDeletionRecord(
            former_workspace_id=former_workspace_id,
            provider=provider,
            normalized_provider_league_id=normalized_provider_league_id,
            requested_at=requested_at,
            scheduled_for=scheduled_for,
            completed_at=completed_at,
            outcome=WorkspaceDeletionOutcome.COMPLETED,
            retention_expires_at=retention_expires_at,
        )
        self.session.add(record)
        self.session.flush()

        return record

    def delete_workspace(self, workspace_id: int) -> int:
        """Delete the workspace row; remaining owned rows cascade.

        Cascades provider connections, users (and their sessions and
        tokens), the provider claim, and background jobs, including the
        running deletion job itself.
        """

        result = self.session.execute(
            delete(Installation)
            .where(Installation.id == workspace_id)
            .execution_options(synchronize_session=False)
        )

        return int(result.rowcount or 0)

    @staticmethod
    def _connection_ids(workspace_id: int):
        return select(ProviderConnection.id).where(
            ProviderConnection.installation_id == workspace_id
        )
