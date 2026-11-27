"""Synthetic fixtures for personal League import tests.

Fake ESPN transport only (no live request), synthetic users, workspaces,
provider IDs and credentials. Every created row is tracked and removed.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.auth.cookies import csrf_cookie_name
from app.core.settings import get_settings
from app.db.auth_models import AuthSessionPurpose, RegistrationRateLimit, User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
    FantasyProvider,
    InstallationStatus,
    OnboardingStage,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, Installation, ProviderConnection
from app.db.session import SessionFactory, engine
from app.main import app
from app.providers.credential_keyring import ProviderCredentialKeyring
from app.providers.espn.league_access import EspnPrivateAccess
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.session_repository import SessionRepository
from app.services.league_import import LeagueImportResult, import_league

TRUSTED_ORIGIN = "http://localhost:5173"
ENDPOINT = "/api/league-imports"
SEASON = 2024
LEAGUE_NAME = "Synthetic Import League"
CURRENT_WEEK = 5
REGULAR_SEASON_WEEKS = 14
SYNTHETIC_SWID = "{SYNTHETIC-IMPORT-SWID-0000}"
SYNTHETIC_S2 = "synthetic-import-espn-s2-not-real-0001"
OTHER_S2 = "synthetic-import-espn-s2-other-user-0002"
WRONG_S2 = "synthetic-import-espn-s2-wrong-0003"
SECRETS = (SYNTHETIC_SWID, SYNTHETIC_SWID.strip("{}"), SYNTHETIC_S2, OTHER_S2, WRONG_S2)
KEYRING = ProviderCredentialKeyring(keys={1: Fernet.generate_key().decode()}, current_version=1)
LIMIT = 1_000
ACTIVE_JOB_STATUSES = (
    BackgroundJobStatus.PENDING,
    BackgroundJobStatus.RUNNING,
    BackgroundJobStatus.RETRY_SCHEDULED,
)


def unique_league_id() -> str:
    """A synthetic ESPN League ID unlikely to collide with any other test."""

    return str(random.randint(10**10, 10**12))


# --------------------------------------------------------------------------
# Fake ESPN transport
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")

        return self._body


def league_body(league_id: str, season: int = SEASON) -> dict[str, Any]:
    return {
        "id": int(league_id),
        "seasonId": season,
        "scoringPeriodId": CURRENT_WEEK,
        "status": {"finalScoringPeriod": 17, "currentMatchupPeriod": CURRENT_WEEK},
        "settings": {
            "name": LEAGUE_NAME,
            "scheduleSettings": {"matchupPeriodCount": REGULAR_SEASON_WEEKS},
        },
        "members": [{"id": "synthetic-member", "isLeagueManager": False}],
    }


@dataclass
class FakeEspn:
    """mode: public | private | missing | error500 | timeout | no_metadata | wrong_league.

    In private mode any cookie value in ``valid_s2`` is accepted.
    """

    mode: str = "public"
    valid_s2: set[str] = field(default_factory=lambda: {SYNTHETIC_S2, OTHER_S2})
    calls: list[dict[str, Any]] = field(default_factory=list)
    before_respond: Callable[[], None] | None = None

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})

        if self.before_respond is not None:
            self.before_respond()

        cookies = kwargs.get("cookies")
        league_id = url.rstrip("/").split("/")[-1]

        if self.mode == "timeout":
            raise requests.Timeout("SECRET-PROVIDER-DETAIL timed out")

        if self.mode == "error500":
            return FakeResponse(500, {"messages": ["SECRET-PROVIDER-DETAIL"]})

        if self.mode == "missing":
            return FakeResponse(404, {"messages": ["SECRET-PROVIDER-DETAIL not found"]})

        if self.mode == "private" and (
            cookies is None or cookies.get("espn_s2") not in self.valid_s2
        ):
            return FakeResponse(401, {"messages": ["SECRET-PROVIDER-DETAIL"]})

        params = kwargs.get("params") or {}
        season = int(params.get("seasonId") or url.split("/seasons/")[1].split("/")[0])
        body = league_body(league_id, season)

        if self.mode == "no_metadata":
            body["settings"] = {"name": LEAGUE_NAME}

        if self.mode == "wrong_league":
            body["id"] = int(league_id) + 1

        if "/leagueHistory/" in url:
            return FakeResponse(200, [body])

        return FakeResponse(200, body)


@pytest.fixture(autouse=True)
def forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Live network requests are forbidden in tests.")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


# --------------------------------------------------------------------------
# Synthetic accounts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Caller:
    user_id: int
    session_id: int
    session_token: str
    csrf_token: str
    workspace_id: int


@dataclass
class Registry:
    workspaces: list[int] = field(default_factory=list)
    league_ids: list[str] = field(default_factory=list)
    # Seasons removed during a test (purge simulation) whose jobs must still go.
    season_ids: list[int] = field(default_factory=list)


def create_caller(
    registry: Registry,
    *,
    role: UserRole = UserRole.ADMIN,
    verified: bool = True,
    active: bool = True,
    purpose: AuthSessionPurpose = AuthSessionPurpose.STANDARD,
    registration_status: WorkspaceRegistrationStatus | None = WorkspaceRegistrationStatus.ACTIVE,
    installation_status: InstallationStatus = InstallationStatus.ACTIVE,
    workspace_id: int | None = None,
) -> Caller:
    """One user with one session, in a new workspace unless ``workspace_id`` is given."""

    suffix = uuid4().hex[:12]

    with SessionFactory() as session:
        if workspace_id is None:
            pending = registration_status is WorkspaceRegistrationStatus.DELETION_PENDING
            workspace = Installation(
                application_name="League Import Workspace",
                slug=f"import-{suffix}",
                normalized_slug=f"import-{suffix}",
                timezone="America/Chicago",
                status=installation_status,
                registration_status=registration_status,
                deletion_requested_at=datetime.now(UTC) if pending else None,
                deletion_scheduled_for=datetime.now(UTC) + timedelta(days=7) if pending else None,
                onboarding_stage=OnboardingStage.COMPLETE,
            )
            session.add(workspace)
            session.flush()
            workspace_id = workspace.id
            registry.workspaces.append(workspace_id)

        user = AuthenticationRepository(session).create_user(
            username=f"import-{role.value}-{suffix}",
            display_name="Synthetic Importer",
            email=f"import-{suffix}@example.com" if role is UserRole.ADMIN else None,
            password="correct horse battery staple",
            role=role,
            workspace_id=workspace_id,
        )
        user.email_verified_at = datetime.now(UTC) if verified else None
        session.flush()
        created = SessionRepository(session).create_session(
            user=user, lifetime_hours=1, purpose=purpose
        )
        user.is_active = active
        session.commit()

        return Caller(
            user_id=user.id,
            session_id=created.session.id,
            session_token=created.session_token,
            csrf_token=created.csrf_token,
            workspace_id=workspace_id,
        )


def cleanup(registry: Registry) -> None:
    with SessionFactory() as session:
        season_ids = registry.season_ids + list(
            session.scalars(
                select(Season.id)
                .join(League, League.id == Season.league_id)
                .where(
                    League.provider == FantasyProvider.ESPN,
                    League.provider_league_id.in_(registry.league_ids),
                )
            )
        )
        session.execute(
            delete(BackgroundJob).where(
                BackgroundJob.job_type == BackgroundJobType.LEAGUE_SEASON_SYNC,
                BackgroundJob.idempotency_key.in_(
                    [f"league-season-sync:{season_id}" for season_id in season_ids]
                ),
            )
        )
        session.execute(
            delete(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.season_id.in_(season_ids))
        )
        session.execute(
            delete(League).where(
                League.provider == FantasyProvider.ESPN,
                League.provider_league_id.in_(registry.league_ids),
            )
        )
        user_ids = list(
            session.scalars(select(User.id).where(User.workspace_id.in_(registry.workspaces)))
        )
        session.execute(
            delete(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.user_id.in_(user_ids))
        )
        session.execute(delete(ProviderConnection).where(ProviderConnection.user_id.in_(user_ids)))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.execute(delete(Installation).where(Installation.id.in_(registry.workspaces)))
        session.commit()


@pytest.fixture
def registry() -> Iterator[Registry]:
    created = Registry()

    with SessionFactory() as session:
        existing_limits = set(session.scalars(select(RegistrationRateLimit.id)))

    yield created

    cleanup(created)

    with SessionFactory() as session:
        session.execute(
            delete(RegistrationRateLimit).where(RegistrationRateLimit.id.not_in(existing_limits))
        )
        session.commit()


@pytest.fixture
def league_id(registry: Registry) -> str:
    value = unique_league_id()
    registry.league_ids.append(value)
    return value


# --------------------------------------------------------------------------
# Invocation
# --------------------------------------------------------------------------


def private(espn_s2: str = SYNTHETIC_S2) -> EspnPrivateAccess:
    return EspnPrivateAccess(swid=SYNTHETIC_SWID, espn_s2=espn_s2)


def run_import(
    caller: Caller,
    league_id: str,
    espn: FakeEspn,
    *,
    private_access: EspnPrivateAccess | None = None,
    season_year: int = SEASON,
    user_maximum: int = LIMIT,
    source: str | None = "203.0.113.10",
) -> LeagueImportResult:
    """Call the service directly with test limits and keyring."""

    return import_league(
        SessionFactory,
        user_id=caller.user_id,
        session_id=caller.session_id,
        source=source,
        provider_league_id=league_id,
        season_year=season_year,
        private_access=private_access,
        credential_keyring=KEYRING,
        throttle_secret="synthetic-throttle-secret",
        user_maximum=user_maximum,
        source_maximum=LIMIT,
        window_minutes=60,
        http_get=espn,
    )


def post_import(
    caller: Caller | None,
    body: dict[str, Any],
    *,
    origin: str | None = TRUSTED_ORIGIN,
    referer: str | None = None,
    csrf: bool = True,
):
    """POST through the real API with real session, CSRF and Origin handling.

    The caller must install ``get_espn_http_get`` and ``get_settings``
    overrides (see ``api_espn``).
    """

    with TestClient(app) as client:
        headers: dict[str, str] = {}

        if origin is not None:
            headers["Origin"] = origin

        if referer is not None:
            headers["Referer"] = referer

        if caller is not None:
            settings = get_settings()
            client.cookies.set(settings.session_cookie_name, caller.session_token)
            client.cookies.set(csrf_cookie_name(settings), caller.csrf_token)

            if csrf:
                headers["X-CSRF-Token"] = caller.csrf_token

        return client.post(ENDPOINT, headers=headers, json=body)


def import_body(league_id: str, **extra: Any) -> dict[str, Any]:
    return {"provider": "espn", "provider_league_id": league_id, "season_year": SEASON, **extra}


def credentials_body(espn_s2: str = SYNTHETIC_S2) -> dict[str, str]:
    return {"swid": SYNTHETIC_SWID, "espn_s2": espn_s2}


# --------------------------------------------------------------------------
# State inspection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportState:
    leagues: int
    seasons: int
    access: int
    connections: int
    active_jobs: int


def import_state(league_id: str, *, user_ids: tuple[int, ...] = ()) -> ImportState:
    with SessionFactory() as session:
        league_ids = select(League.id).where(
            League.provider == FantasyProvider.ESPN, League.provider_league_id == league_id
        )
        season_ids = list(
            session.scalars(select(Season.id).where(Season.league_id.in_(league_ids)))
        )

        def count(statement) -> int:
            return int(session.scalar(statement) or 0)

        return ImportState(
            leagues=count(
                select(func.count()).select_from(League).where(League.id.in_(league_ids))
            ),
            seasons=len(season_ids),
            access=count(
                select(func.count())
                .select_from(UserLeagueSeasonAccess)
                .where(UserLeagueSeasonAccess.season_id.in_(season_ids))
            ),
            connections=count(
                select(func.count())
                .select_from(ProviderConnection)
                .where(
                    ProviderConnection.user_id.in_(user_ids),
                    ProviderConnection.provider_league_id == league_id,
                )
            ),
            active_jobs=count(
                select(func.count())
                .select_from(BackgroundJob)
                .where(
                    BackgroundJob.idempotency_key.in_(
                        [f"league-season-sync:{season_id}" for season_id in season_ids]
                    ),
                    BackgroundJob.status.in_(ACTIVE_JOB_STATUSES),
                )
            ),
        )


def access_row(user_id: int, season_id: int) -> UserLeagueSeasonAccess | None:
    with SessionFactory() as session:
        row = session.scalar(
            select(UserLeagueSeasonAccess).where(
                UserLeagueSeasonAccess.user_id == user_id,
                UserLeagueSeasonAccess.season_id == season_id,
            )
        )
        if row is not None:
            session.expunge(row)
        return row


def user_connections(user_id: int) -> list[ProviderConnection]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(ProviderConnection)
                .where(ProviderConnection.user_id == user_id)
                .order_by(ProviderConnection.id)
            )
        )
        session.expunge_all()
        return rows


def preference(user_id: int) -> int | None:
    with SessionFactory() as session:
        row = session.get(UserPreference, user_id)
        return row.default_league_season_id if row is not None else None


def checked_out_connections() -> int:
    return engine.pool.checkedout()


@pytest.fixture
def espn() -> FakeEspn:
    return FakeEspn()


@pytest.fixture
def api_espn(espn: FakeEspn) -> Iterator[FakeEspn]:
    """Route the API through the fake ESPN transport and a synthetic keyring."""

    from pydantic import SecretStr

    from app.api.auth_dependencies import get_current_user
    from app.api.onboarding import get_espn_http_get

    base = get_settings()
    key = KEYRING.keys[1]
    inherited_user = app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_espn_http_get] = lambda: espn
    app.dependency_overrides[get_settings] = lambda: base.model_copy(
        update={
            "provider_credential_keys": SecretStr(f'{{"1": "{key}"}}'),
            "provider_credential_current_key_version": 1,
            "provider_validation_workspace_maximum_requests": 50,
            "provider_validation_source_maximum_requests": 200,
        }
    )

    try:
        yield espn
    finally:
        app.dependency_overrides.pop(get_espn_http_get, None)
        app.dependency_overrides.pop(get_settings, None)
        if inherited_user is not None:
            app.dependency_overrides[get_current_user] = inherited_user
