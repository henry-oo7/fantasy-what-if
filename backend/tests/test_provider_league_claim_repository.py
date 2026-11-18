from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import League
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderLeagueClaim,
)
from app.db.session import engine
from app.repositories.provider_league_claim_repository import (
    ProviderLeagueAlreadyClaimedError,
    ProviderLeagueClaimRepository,
    normalize_provider_league_id,
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


def create_workspace(
    session: Session,
) -> Installation:
    slug = f"claim-{uuid4().hex[:12]}"

    workspace = Installation(
        application_name="Claim Repository Test",
        slug=slug,
        normalized_slug=slug,
        timezone="America/Chicago",
        status=InstallationStatus.SETUP_IN_PROGRESS,
    )
    session.add(workspace)
    session.flush()

    return workspace


def test_provider_league_id_is_trimmed() -> None:
    assert normalize_provider_league_id("  League-ABC  ") == "League-ABC"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
    ],
)
def test_empty_provider_league_id_is_rejected(
    value: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        normalize_provider_league_id(value)


def test_claim_is_created_without_committing(
    database_session: Session,
) -> None:
    workspace = create_workspace(database_session)
    repository = ProviderLeagueClaimRepository(database_session)

    claim = repository.create_claim(
        workspace_id=workspace.id,
        provider=FantasyProvider.SLEEPER,
        provider_league_id="  league-alpha  ",
    )

    assert claim.id is not None
    assert claim.workspace_id == workspace.id
    assert claim.league_id is None
    assert claim.provider is FantasyProvider.SLEEPER
    assert claim.provider_league_id == "league-alpha"
    assert claim.normalized_provider_league_id == "league-alpha"
    assert database_session.in_transaction()


def test_duplicate_provider_identity_is_rejected(
    database_session: Session,
) -> None:
    first_workspace = create_workspace(database_session)
    second_workspace = create_workspace(database_session)
    repository = ProviderLeagueClaimRepository(database_session)

    repository.create_claim(
        workspace_id=first_workspace.id,
        provider=FantasyProvider.SLEEPER,
        provider_league_id="league-shared",
    )

    with pytest.raises(
        ProviderLeagueAlreadyClaimedError,
        match="already connected",
    ):
        repository.create_claim(
            workspace_id=second_workspace.id,
            provider=FantasyProvider.SLEEPER,
            provider_league_id=" league-shared ",
        )

    assert database_session.in_transaction()

    claims = database_session.scalars(
        select(ProviderLeagueClaim).where(
            ProviderLeagueClaim.provider == FantasyProvider.SLEEPER,
            ProviderLeagueClaim.normalized_provider_league_id == "league-shared",
        )
    ).all()

    assert len(claims) == 1
    assert claims[0].workspace_id == first_workspace.id


def test_same_raw_id_is_allowed_for_different_providers(
    database_session: Session,
) -> None:
    first_workspace = create_workspace(database_session)
    second_workspace = create_workspace(database_session)
    repository = ProviderLeagueClaimRepository(database_session)

    espn_claim = repository.create_claim(
        workspace_id=first_workspace.id,
        provider=FantasyProvider.ESPN,
        provider_league_id="shared-id",
    )
    sleeper_claim = repository.create_claim(
        workspace_id=second_workspace.id,
        provider=FantasyProvider.SLEEPER,
        provider_league_id="shared-id",
    )

    assert espn_claim.id != sleeper_claim.id


def test_claim_can_attach_one_league(
    database_session: Session,
) -> None:
    workspace = create_workspace(database_session)
    repository = ProviderLeagueClaimRepository(database_session)

    claim = repository.create_claim(
        workspace_id=workspace.id,
        provider=FantasyProvider.YAHOO,
        provider_league_id="yahoo-league",
    )

    league = League(
        installation_id=workspace.id,
        provider=FantasyProvider.YAHOO,
        provider_league_id="yahoo-league",
        name="Yahoo Claim Test League",
    )
    database_session.add(league)
    database_session.flush()

    repository.attach_league(
        claim,
        league_id=league.id,
    )

    assert claim.league_id == league.id
