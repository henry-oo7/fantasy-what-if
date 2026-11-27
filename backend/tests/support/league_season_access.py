"""Synthetic fixtures for personal League Season authorization tests.

Reuses the League-import callers and registry (synthetic users, workspaces
and provider IDs, all tracked and removed). Seeds canonical Seasons with
distinctive per-Season team names, matchups, schedule rules and optimal
lineup results so cross-Season leakage is visible in responses.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth_dependencies import get_current_user
from app.auth.cookies import csrf_cookie_name
from app.core.settings import get_settings
from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Matchup, OptimalLineupResult, ScheduleRule, Season, Team
from app.db.platform_enums import (
    FantasyProvider,
    SeasonStatus,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import ProviderLeagueClaim
from app.db.session import SessionFactory
from app.main import app
from app.repositories.optimal_lineup_repository import OPTIMAL_LINEUP_CALCULATION_VERSION
from tests.support.league_import import TRUSTED_ORIGIN, Caller, Registry, unique_league_id

SYNCED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PREFERENCE_ENDPOINT = "/api/preferences/default-season"


@pytest.fixture(autouse=True)
def real_workspace_authentication() -> Iterator[None]:
    """Use real sessions for workspace routes too (conftest fakes ``get_current_user``)."""

    inherited = app.dependency_overrides.pop(get_current_user, None)
    try:
        yield
    finally:
        if inherited is not None:
            app.dependency_overrides[get_current_user] = inherited


@dataclass(frozen=True)
class SeededSeason:
    season_id: int
    league_pk: int
    provider_league_id: str
    year: int
    team_ids: tuple[int, ...]
    marker: str


def seed_season(
    registry: Registry,
    *,
    provider_league_id: str | None = None,
    year: int = 2024,
    sync_state: SeasonSyncState | None = SeasonSyncState.FRESH,
    data: bool = True,
    workspace_id: int | None = None,
    status: SeasonStatus = SeasonStatus.DRAFT,
    league_name: str | None = None,
) -> SeededSeason:
    """One canonical Season. ``data`` records a successful sync and stores rows.

    ``workspace_id`` sets ``installation_id`` and that workspace's matching
    claim (temporary workspace authority) when the League is created here.
    """

    if provider_league_id is None:
        provider_league_id = unique_league_id()
    if provider_league_id not in registry.league_ids:
        registry.league_ids.append(provider_league_id)

    marker = f"S{provider_league_id[-6:]}-{year}"

    with SessionFactory() as session:
        league = session.scalar(
            select(League).where(
                League.provider == FantasyProvider.ESPN,
                League.provider_league_id == provider_league_id,
            )
        )

        if league is None:
            league = League(
                name=league_name or f"League {provider_league_id[-6:]}",
                provider=FantasyProvider.ESPN,
                provider_league_id=provider_league_id,
                installation_id=workspace_id,
            )
            session.add(league)
            session.flush()

            if workspace_id is not None:
                session.add(
                    ProviderLeagueClaim(
                        workspace_id=workspace_id,
                        league_id=league.id,
                        provider=FantasyProvider.ESPN,
                        provider_league_id=provider_league_id,
                        normalized_provider_league_id=provider_league_id,
                    )
                )

        season = Season(
            league_id=league.id,
            year=year,
            current_week=3,
            regular_season_weeks=4,
            provider_season_id=str(year),
            status=status,
            sync_state=sync_state,
            data_version=1 if data else 0,
            last_successful_sync_at=SYNCED_AT if data else None,
            last_synced_at=SYNCED_AT if data else None,
        )
        session.add(season)
        session.flush()

        team_ids: tuple[int, ...] = ()

        if data:
            teams = [
                Team(
                    season_id=season.id,
                    provider_team_id=str(index),
                    name=f"{marker} Team {index}",
                    wins=index,
                    losses=2 - index,
                    ties=0,
                    points_for=Decimal("100.00") + index,
                    points_against=Decimal("90.00"),
                )
                for index in (1, 2)
            ]
            session.add_all(teams)
            session.flush()
            team_ids = tuple(team.id for team in teams)
            home, away = team_ids

            for week in (1, 2, 3, 4):
                complete = week < 3
                session.add(
                    Matchup(
                        season_id=season.id,
                        week=week,
                        home_team_id=home if week % 2 else away,
                        away_team_id=away if week % 2 else home,
                        home_score=Decimal("101.50") if complete else None,
                        away_score=Decimal("99.25") if complete else None,
                        is_complete=complete,
                        is_bye=False,
                    )
                )

            session.add(
                ScheduleRule(
                    season_id=season.id,
                    regular_season_weeks=4,
                    playoff_team_count=2,
                    playoff_seed_tie_rule="TOTAL_POINTS_SCORED",
                )
            )

            for team in teams:
                session.add(
                    OptimalLineupResult(
                        season_id=season.id,
                        team_id=team.id,
                        week=1,
                        team_name=team.name,
                        actual_points=Decimal("100.00"),
                        optimal_points=Decimal("100.00"),
                        missed_points=Decimal("0.00"),
                        efficiency_percentage=Decimal("100.00"),
                        actual_starter_count=1,
                        optimal_filled_slot_count=1,
                        calculation_version=OPTIMAL_LINEUP_CALCULATION_VERSION,
                    )
                )

        session.commit()

        return SeededSeason(
            season_id=season.id,
            league_pk=league.id,
            provider_league_id=provider_league_id,
            year=year,
            team_ids=team_ids,
            marker=marker,
        )


def grant(
    user_id: int,
    season_id: int,
    status: UserSeasonAccessStatus = UserSeasonAccessStatus.ACTIVE,
) -> None:
    with SessionFactory() as session:
        session.add(
            UserLeagueSeasonAccess(
                user_id=user_id,
                season_id=season_id,
                proof_mode=UserSeasonProofMode.PUBLIC,
                status=status,
                verified_at=SYNCED_AT,
            )
        )
        session.commit()


def set_default(user_id: int, season_id: int | None) -> None:
    with SessionFactory() as session:
        preference = session.get(UserPreference, user_id)
        if preference is None:
            preference = UserPreference(user_id=user_id)
            session.add(preference)
        preference.default_league_season_id = season_id
        session.commit()


def stored_default(user_id: int) -> int | None:
    with SessionFactory() as session:
        return session.scalar(
            select(UserPreference.default_league_season_id).where(UserPreference.user_id == user_id)
        )


def _client(caller: Caller | None) -> TestClient:
    client = TestClient(app)
    if caller is not None:
        settings = get_settings()
        client.cookies.set(settings.session_cookie_name, caller.session_token)
        client.cookies.set(csrf_cookie_name(settings), caller.csrf_token)
    return client


def get(caller: Caller | None, path: str, **params: Any):
    with _client(caller) as client:
        return client.get(path, params=params or None)


def put_default(
    caller: Caller | None,
    body: dict[str, Any],
    *,
    origin: str | None = TRUSTED_ORIGIN,
    csrf: bool = True,
):
    with _client(caller) as client:
        headers: dict[str, str] = {}
        if origin is not None:
            headers["Origin"] = origin
        if caller is not None and csrf:
            headers["X-CSRF-Token"] = caller.csrf_token
        return client.put(PREFERENCE_ENDPOINT, headers=headers, json=body)


def season_paths(season: SeededSeason) -> list[tuple[str, dict[str, Any]]]:
    """Every personal Season-scoped GET with valid parameters for ``season``."""

    base = f"/api/seasons/{season.season_id}"
    first, second = season.team_ids or (1, 2)
    return [
        (base, {}),
        (f"{base}/snapshot", {}),
        (f"{base}/playoff-predictor", {}),
        (f"{base}/analytics/all-play", {}),
        (f"{base}/analytics/league-average", {}),
        (f"{base}/analytics/schedule-swap", {"first_team_id": first, "second_team_id": second}),
        (f"{base}/analytics/optimal-lineups", {}),
        (f"{base}/analytics/optimal-lineups/{first}", {}),
    ]
