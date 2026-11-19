import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import League, Season
from app.db.platform_enums import FantasyProvider
from app.db.platform_models import (
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.db.session import engine
from app.services.provider_adapter_resolver import (
    resolve_connection_credentials,
)
from tests.support.multi_workspace_espn import (
    FIRST_COOKIE_MARKER,
    FIRST_PROVIDER_LEAGUE_ID,
    FIRST_SWID_MARKER,
    SECOND_COOKIE_MARKER,
    SECOND_PROVIDER_LEAGUE_ID,
    SECOND_SWID_MARKER,
    create_multi_workspace_espn_fixture,
)


@pytest.fixture
def database_session():
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


def test_fixture_creates_two_isolated_espn_workspaces(
    database_session: Session,
) -> None:
    fixture = create_multi_workspace_espn_fixture(database_session)

    assert fixture.first.workspace_id != fixture.second.workspace_id
    assert fixture.first.provider_connection_id != fixture.second.provider_connection_id
    assert fixture.first.league_id != fixture.second.league_id
    assert fixture.first.season_id != fixture.second.season_id
    assert fixture.first.provider_league_id != fixture.second.provider_league_id

    first_workspace = database_session.get(
        Installation,
        fixture.first.workspace_id,
    )
    second_workspace = database_session.get(
        Installation,
        fixture.second.workspace_id,
    )

    assert first_workspace is not None
    assert second_workspace is not None
    assert first_workspace.active_league_id == (fixture.first.league_id)
    assert first_workspace.active_season_id == (fixture.first.season_id)
    assert second_workspace.active_league_id == (fixture.second.league_id)
    assert second_workspace.active_season_id == (fixture.second.season_id)


def test_fixture_creates_exclusive_provider_claims(
    database_session: Session,
) -> None:
    fixture = create_multi_workspace_espn_fixture(database_session)

    claims = tuple(
        database_session.scalars(
            select(ProviderLeagueClaim)
            .where(
                ProviderLeagueClaim.workspace_id.in_(
                    (
                        fixture.first.workspace_id,
                        fixture.second.workspace_id,
                    )
                )
            )
            .order_by(ProviderLeagueClaim.workspace_id)
        )
    )

    assert len(claims) == 2
    assert {claim.provider_league_id for claim in claims} == {
        FIRST_PROVIDER_LEAGUE_ID,
        SECOND_PROVIDER_LEAGUE_ID,
    }
    assert all(claim.provider is FantasyProvider.ESPN for claim in claims)
    assert len({claim.league_id for claim in claims}) == 2


def test_fixture_stores_distinct_encrypted_credentials(
    database_session: Session,
) -> None:
    fixture = create_multi_workspace_espn_fixture(database_session)

    first_connection = database_session.get(
        ProviderConnection,
        fixture.first.provider_connection_id,
    )
    second_connection = database_session.get(
        ProviderConnection,
        fixture.second.provider_connection_id,
    )
    first_season = database_session.get(
        Season,
        fixture.first.season_id,
    )
    second_season = database_session.get(
        Season,
        fixture.second.season_id,
    )

    assert first_connection is not None
    assert second_connection is not None
    assert first_season is not None
    assert second_season is not None

    first_ciphertext = first_connection.encrypted_credentials
    second_ciphertext = second_connection.encrypted_credentials

    assert first_ciphertext is not None
    assert second_ciphertext is not None
    assert first_ciphertext != second_ciphertext

    for secret in (
        FIRST_COOKIE_MARKER,
        FIRST_SWID_MARKER,
        SECOND_COOKIE_MARKER,
        SECOND_SWID_MARKER,
    ):
        assert secret not in first_ciphertext
        assert secret not in second_ciphertext

    first_credentials, first_source = resolve_connection_credentials(
        connection=first_connection,
        season=first_season,
        settings=fixture.settings(),
    )
    second_credentials, second_source = resolve_connection_credentials(
        connection=second_connection,
        season=second_season,
        settings=fixture.settings(),
    )

    assert first_source == "stored"
    assert second_source == "stored"
    assert dict(first_credentials.values) == {
        "league_id": FIRST_PROVIDER_LEAGUE_ID,
        "season": "2026",
        "espn_s2": FIRST_COOKIE_MARKER,
        "swid": FIRST_SWID_MARKER,
    }
    assert dict(second_credentials.values) == {
        "league_id": SECOND_PROVIDER_LEAGUE_ID,
        "season": "2026",
        "espn_s2": SECOND_COOKIE_MARKER,
        "swid": SECOND_SWID_MARKER,
    }


def test_fixture_relationships_remain_workspace_owned(
    database_session: Session,
) -> None:
    fixture = create_multi_workspace_espn_fixture(database_session)

    first_league = database_session.get(
        League,
        fixture.first.league_id,
    )
    second_league = database_session.get(
        League,
        fixture.second.league_id,
    )
    first_season = database_session.get(
        Season,
        fixture.first.season_id,
    )
    second_season = database_session.get(
        Season,
        fixture.second.season_id,
    )

    assert first_league is not None
    assert second_league is not None
    assert first_season is not None
    assert second_season is not None

    assert first_league.installation_id == (fixture.first.workspace_id)
    assert second_league.installation_id == (fixture.second.workspace_id)
    assert first_season.league_id == (fixture.first.league_id)
    assert second_season.league_id == (fixture.second.league_id)
    assert first_season.provider_connection_id == fixture.first.provider_connection_id
    assert second_season.provider_connection_id == fixture.second.provider_connection_id
