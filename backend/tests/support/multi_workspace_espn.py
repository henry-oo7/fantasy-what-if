"""Reusable two-workspace ESPN integration fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    FantasySport,
    InstallationStatus,
    SeasonStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderConnection,
    ProviderLeagueClaim,
)
from app.providers.credential_keyring import (
    ProviderCredentialKeyring,
)
from app.providers.credential_vault import (
    ProviderCredentialVault,
)
from app.providers.espn import credentials_from_values

TEST_CREDENTIAL_VERSION = 1

SECOND_WORKSPACE_ID = 200_001
SECOND_CONNECTION_ID = 200_001
SECOND_LEAGUE_ID = 200_001
SECOND_SEASON_ID = 200_001
SECOND_CLAIM_ID = 200_001

FIRST_PROVIDER_LEAGUE_ID = "espn-test-league-alpha"
SECOND_PROVIDER_LEAGUE_ID = "espn-test-league-beta"

FIRST_COOKIE_MARKER = "synthetic-cookie-alpha"
SECOND_COOKIE_MARKER = "synthetic-cookie-beta"

FIRST_SWID_MARKER = "{synthetic-swid-alpha}"
SECOND_SWID_MARKER = "{synthetic-swid-beta}"


@dataclass(frozen=True, slots=True)
class EspnWorkspaceFixture:
    """Identifiers and synthetic credential markers for one workspace."""

    workspace_id: int
    provider_connection_id: int
    league_id: int
    season_id: int
    provider_league_id: str
    provider_season_id: str
    cookie_marker: str
    swid_marker: str


@dataclass(frozen=True, slots=True)
class MultiWorkspaceEspnFixture:
    """Two isolated ESPN workspace fixtures sharing one vault key."""

    credential_key: str
    first: EspnWorkspaceFixture
    second: EspnWorkspaceFixture

    def settings(self):
        """Return resolver-compatible credential settings."""

        from types import SimpleNamespace

        keyring = ProviderCredentialKeyring(
            keys={
                TEST_CREDENTIAL_VERSION: self.credential_key,
            },
            current_version=TEST_CREDENTIAL_VERSION,
        )

        return SimpleNamespace(
            provider_credential_keyring=lambda: keyring,
            espn_s2=None,
            espn_swid=None,
        )


def create_multi_workspace_espn_fixture(
    session: Session,
) -> MultiWorkspaceEspnFixture:
    """Create two active ESPN workspaces with encrypted credentials."""

    timestamp = datetime.now(UTC)
    credential_key = Fernet.generate_key().decode("ascii")
    vault = ProviderCredentialVault(
        keys={
            TEST_CREDENTIAL_VERSION: credential_key,
        }
    )

    first_workspace = session.get(
        Installation,
        1,
    )
    first_connection = session.get(
        ProviderConnection,
        1,
    )
    first_league = session.get(
        League,
        1,
    )
    first_season = session.get(
        Season,
        1,
    )

    if (
        first_workspace is None
        or first_connection is None
        or first_league is None
        or first_season is None
    ):
        raise RuntimeError("The canonical workspace-one test baseline is unavailable.")

    first_league.provider = FantasyProvider.ESPN
    first_league.provider_league_id = FIRST_PROVIDER_LEAGUE_ID
    first_season.provider_connection_id = first_connection.id
    first_season.provider_season_id = "2026"
    first_season.status = SeasonStatus.ACTIVE

    first_connection.provider = FantasyProvider.ESPN
    first_connection.credential_version = TEST_CREDENTIAL_VERSION
    first_connection.is_active = True
    first_connection.encrypted_credentials = vault.encrypt(
        provider=FantasyProvider.ESPN,
        credentials=credentials_from_values(
            league_id=FIRST_PROVIDER_LEAGUE_ID,
            season="2026",
            espn_s2=FIRST_COOKIE_MARKER,
            swid=FIRST_SWID_MARKER,
        ),
        key_version=TEST_CREDENTIAL_VERSION,
    )

    second_workspace = Installation(
        id=SECOND_WORKSPACE_ID,
        application_name="Second ESPN Workspace",
        public_url=("https://second-workspace.test.example"),
        slug="second-espn-workspace",
        normalized_slug="second-espn-workspace",
        timezone="America/Chicago",
        status=InstallationStatus.ACTIVE,
        setup_started_at=timestamp,
        setup_completed_at=timestamp,
    )
    session.add(second_workspace)
    session.flush()

    second_connection = ProviderConnection(
        id=SECOND_CONNECTION_ID,
        installation_id=SECOND_WORKSPACE_ID,
        provider=FantasyProvider.ESPN,
        display_name="Second Synthetic ESPN Connection",
        encrypted_credentials=vault.encrypt(
            provider=FantasyProvider.ESPN,
            credentials=credentials_from_values(
                league_id=SECOND_PROVIDER_LEAGUE_ID,
                season="2026",
                espn_s2=SECOND_COOKIE_MARKER,
                swid=SECOND_SWID_MARKER,
            ),
            key_version=TEST_CREDENTIAL_VERSION,
        ),
        credential_version=TEST_CREDENTIAL_VERSION,
        is_active=True,
        last_validated_at=timestamp,
        last_validation_error=None,
    )
    session.add(second_connection)
    session.flush()

    second_league = League(
        id=SECOND_LEAGUE_ID,
        name="Second Synthetic ESPN League",
        installation_id=SECOND_WORKSPACE_ID,
        provider=FantasyProvider.ESPN,
        provider_league_id=(SECOND_PROVIDER_LEAGUE_ID),
        sport=FantasySport.FOOTBALL,
        logo_url=None,
    )
    session.add(second_league)
    session.flush()

    second_season = Season(
        id=SECOND_SEASON_ID,
        league_id=SECOND_LEAGUE_ID,
        year=2026,
        current_week=9,
        regular_season_weeks=12,
        last_synced_at=None,
        provider_connection_id=(SECOND_CONNECTION_ID),
        provider_season_id="2026",
        status=SeasonStatus.ACTIVE,
        team_count=2,
        playoff_week_start=None,
        playoff_week_end=None,
        scoring_type="head_to_head_points",
        supports_head_to_head=True,
        supports_weekly_matchups=True,
        supports_lineup_history=True,
        supports_optimal_lineups=False,
        supports_schedule_swap=True,
        supports_all_play=True,
        supports_league_average=True,
        supports_team_logos=False,
        configuration_snapshot={},
        activated_at=timestamp,
    )
    session.add(second_season)
    session.flush()

    first_claim = session.scalar(
        select(ProviderLeagueClaim).where(
            ProviderLeagueClaim.workspace_id == first_workspace.id,
        )
    )

    if first_claim is None:
        first_claim = ProviderLeagueClaim(
            workspace_id=first_workspace.id,
            league_id=first_league.id,
            provider=FantasyProvider.ESPN,
            provider_league_id=(FIRST_PROVIDER_LEAGUE_ID),
            normalized_provider_league_id=(FIRST_PROVIDER_LEAGUE_ID),
        )
        session.add(first_claim)
    else:
        first_claim.league_id = first_league.id
        first_claim.provider = FantasyProvider.ESPN
        first_claim.provider_league_id = FIRST_PROVIDER_LEAGUE_ID
        first_claim.normalized_provider_league_id = FIRST_PROVIDER_LEAGUE_ID

    second_claim = session.scalar(
        select(ProviderLeagueClaim).where(
            ProviderLeagueClaim.workspace_id == SECOND_WORKSPACE_ID,
        )
    )

    if second_claim is None:
        second_claim = ProviderLeagueClaim(
            id=SECOND_CLAIM_ID,
            workspace_id=SECOND_WORKSPACE_ID,
            league_id=SECOND_LEAGUE_ID,
            provider=FantasyProvider.ESPN,
            provider_league_id=(SECOND_PROVIDER_LEAGUE_ID),
            normalized_provider_league_id=(SECOND_PROVIDER_LEAGUE_ID),
        )
        session.add(second_claim)
    else:
        second_claim.league_id = SECOND_LEAGUE_ID
        second_claim.provider = FantasyProvider.ESPN
        second_claim.provider_league_id = SECOND_PROVIDER_LEAGUE_ID
        second_claim.normalized_provider_league_id = SECOND_PROVIDER_LEAGUE_ID

    session.flush()

    first_workspace.active_league_id = first_league.id
    first_workspace.active_season_id = first_season.id
    second_workspace.active_league_id = second_league.id
    second_workspace.active_season_id = second_season.id
    session.flush()

    return MultiWorkspaceEspnFixture(
        credential_key=credential_key,
        first=EspnWorkspaceFixture(
            workspace_id=first_workspace.id,
            provider_connection_id=(first_connection.id),
            league_id=first_league.id,
            season_id=first_season.id,
            provider_league_id=(FIRST_PROVIDER_LEAGUE_ID),
            provider_season_id="2026",
            cookie_marker=FIRST_COOKIE_MARKER,
            swid_marker=FIRST_SWID_MARKER,
        ),
        second=EspnWorkspaceFixture(
            workspace_id=second_workspace.id,
            provider_connection_id=(second_connection.id),
            league_id=second_league.id,
            season_id=second_season.id,
            provider_league_id=(SECOND_PROVIDER_LEAGUE_ID),
            provider_season_id="2026",
            cookie_marker=SECOND_COOKIE_MARKER,
            swid_marker=SECOND_SWID_MARKER,
        ),
    )
