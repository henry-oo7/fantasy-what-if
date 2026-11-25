from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import League
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import ProviderLeagueClaim

PROVIDER_IDENTITY_CONSTRAINT = "uq_provider_league_claims_provider_identity"


def workspace_league_condition(workspace_id: Any):
    """``League.installation_id`` equals the workspace AND that workspace's claim matches.

    Temporary workspace authority (ADR 0010 compatibility); never personal
    access. ``installation_id`` alone is compatibility metadata and never
    authorizes. ``workspace_id`` is an int or a column; correlates to ``League``.
    """

    return and_(
        League.installation_id == workspace_id,
        exists().where(
            ProviderLeagueClaim.workspace_id == workspace_id,
            ProviderLeagueClaim.provider == League.provider,
            ProviderLeagueClaim.normalized_provider_league_id == League.provider_league_id,
        ),
    )


def claimed_workspace_id(session: Session, league: League) -> int | None:
    """The League's ``installation_id`` if that workspace's claim backs it, else None."""

    workspace_id = league.installation_id

    if workspace_id is None or not session.scalar(
        select(League.id).where(League.id == league.id, workspace_league_condition(workspace_id))
    ):
        return None

    return workspace_id


class ProviderLeagueAlreadyClaimedError(ValueError):
    """The provider league belongs to another workspace."""


class ProviderLeagueClaimRepository:
    def __init__(
        self,
        session: Session,
    ) -> None:
        self.session = session

    def create_claim(
        self,
        *,
        workspace_id: int,
        provider: FantasyProvider,
        provider_league_id: str,
    ) -> ProviderLeagueClaim:
        normalized_id = normalize_provider_league_id(provider_league_id)

        claim = ProviderLeagueClaim(
            workspace_id=workspace_id,
            provider=provider,
            provider_league_id=normalized_id,
            normalized_provider_league_id=normalized_id,
        )

        try:
            with self.session.begin_nested():
                self.session.add(claim)
                self.session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == PROVIDER_IDENTITY_CONSTRAINT:
                raise ProviderLeagueAlreadyClaimedError(
                    "This league is already connected."
                ) from exc

            raise

        return claim

    def get_claim(
        self,
        *,
        provider: FantasyProvider,
        provider_league_id: str,
    ) -> ProviderLeagueClaim | None:
        normalized_id = normalize_provider_league_id(provider_league_id)

        return self.session.scalar(
            select(ProviderLeagueClaim).where(
                ProviderLeagueClaim.provider == provider,
                ProviderLeagueClaim.normalized_provider_league_id == normalized_id,
            )
        )

    def attach_league(
        self,
        claim: ProviderLeagueClaim,
        *,
        league_id: int,
    ) -> None:
        if claim.league_id is not None:
            if claim.league_id == league_id:
                return

            raise ValueError("The provider league claim is already attached to another league.")

        claim.league_id = league_id
        self.session.flush()


def normalize_provider_league_id(
    provider_league_id: str,
) -> str:
    normalized = provider_league_id.strip()

    if not normalized:
        raise ValueError("Provider league ID cannot be empty.")

    if len(normalized) > 255:
        raise ValueError("Provider league ID cannot exceed 255 characters.")

    return normalized


def _constraint_name(
    error: IntegrityError,
) -> str | None:
    original = error.orig
    diagnostic = getattr(
        original,
        "diag",
        None,
    )

    return getattr(
        diagnostic,
        "constraint_name",
        None,
    )
