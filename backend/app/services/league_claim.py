"""Confirm and atomically claim the validated provider league target.

Claim identity is provider plus normalized provider League ID; season is
not part of uniqueness. The unique constraint
``uq_provider_league_claims_provider_identity`` is the concurrency
authority. Any verified administrator may claim; league-manager,
commissioner and team-owner status are never checked.

Transaction boundary: one transaction per confirmation.

1. Lock the workspace row, re-authorize, and require league_claim_pending.
2. Lock the active provider connection and require a complete target.
3. Insert the claim inside a savepoint.
4. Success: advance to initial_sync_pending and commit.
5. Provider-identity conflict: roll back only the savepoint, delete the
   losing provisional workspace (the database cascades users, sessions,
   account tokens, provider connections, claims, leagues, seasons and
   jobs), and commit in the same transaction (ADR 0006).

A concurrent second insert blocks on the unique index until the first
transaction finishes, then either conflicts (first committed) or
succeeds (first rolled back), so exactly one claim can win.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.platform_enums import (
    FantasyProvider,
    OnboardingStage,
    ProviderAccessMode,
)
from app.db.platform_models import Installation, ProviderConnection
from app.repositories.provider_league_claim_repository import (
    ProviderLeagueAlreadyClaimedError,
    ProviderLeagueClaimRepository,
)
from app.services.provider_onboarding import (
    ProviderOnboardingError,
    authorize_onboarding_administrator,
)

CLAIM_STAGES = frozenset({OnboardingStage.LEAGUE_CLAIM_PENDING})

LEAGUE_ALREADY_CLAIMED_DETAIL = (
    "This fantasy league is already connected to Fantasy What-If. Contact the league "
    "owner and ask for the workspace's spectator access if you need viewing access."
)


class LeagueAlreadyClaimedError(Exception):
    """Another workspace holds the claim; the losing registration was removed."""


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    """Safe validated target. Never carries credentials or key versions."""

    provider: FantasyProvider
    league_id: str
    season_year: int
    access_mode: ProviderAccessMode


def claim_stage_invalid() -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=409,
        code="onboarding_stage_invalid",
        detail="This league cannot be connected at this onboarding step.",
        retryable=False,
    )


def target_unavailable() -> ProviderOnboardingError:
    return ProviderOnboardingError(
        status_code=409,
        code="provider_target_unavailable",
        detail="Check your league access again before connecting the league.",
        retryable=False,
    )


def _authorize(session: Session, *, user_id: int, workspace_id: int, lock: bool) -> Installation:
    return authorize_onboarding_administrator(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        allowed_stages=CLAIM_STAGES,
        forbidden_code="provider_claim_forbidden",
        stage_invalid=claim_stage_invalid,
        lock=lock,
    )


def _complete_target(connection: ProviderConnection | None) -> ValidatedTarget:
    if (
        connection is None
        or connection.provider is not FantasyProvider.ESPN
        or connection.access_mode is None
        or connection.provider_league_id is None
        or connection.provider_season_year is None
        or connection.last_validated_at is None
        or (
            connection.access_mode is ProviderAccessMode.PRIVATE
            and (connection.encrypted_credentials is None or connection.credential_version is None)
        )
    ):
        raise target_unavailable()

    return ValidatedTarget(
        provider=connection.provider,
        league_id=connection.provider_league_id,
        season_year=connection.provider_season_year,
        access_mode=connection.access_mode,
    )


def _active_connection_query(workspace_id: int):
    return select(ProviderConnection).where(
        ProviderConnection.installation_id == workspace_id,
        ProviderConnection.is_active.is_(True),
    )


def read_validated_target(session: Session, *, user_id: int, workspace_id: int) -> ValidatedTarget:
    _authorize(session, user_id=user_id, workspace_id=workspace_id, lock=False)

    return _complete_target(session.scalar(_active_connection_query(workspace_id)))


def claim_validated_league(session: Session, *, user_id: int, workspace_id: int) -> ValidatedTarget:
    """Claim the validated target, or delete the losing registration on conflict.

    The caller owns the session; this function commits in both outcomes.
    """

    workspace = _authorize(session, user_id=user_id, workspace_id=workspace_id, lock=True)
    target = _complete_target(
        session.scalar(_active_connection_query(workspace_id).with_for_update())
    )

    try:
        ProviderLeagueClaimRepository(session).create_claim(
            workspace_id=workspace.id,
            provider=target.provider,
            provider_league_id=target.league_id,
        )
    except ProviderLeagueAlreadyClaimedError as exc:
        # Same transaction: the savepoint is already rolled back, the
        # workspace row is still locked, and nothing else was written.
        session.execute(delete(Installation).where(Installation.id == workspace.id))
        session.commit()
        raise LeagueAlreadyClaimedError() from exc

    workspace.onboarding_stage = OnboardingStage.INITIAL_SYNC_PENDING
    session.commit()

    return target
