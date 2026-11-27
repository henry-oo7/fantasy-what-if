# ruff: noqa: F811  (imported pytest fixtures are used as test parameters)
"""Critical invariants of personal League Season authorization (ADR 0010).

Real PostgreSQL, committed synthetic data, real sessions through the API.
Concurrency is coordinated with events and observed lock waits, never sleeps.
The access row is the only authority: workspace, role, claim, connection,
preference and data existence must never grant a personal read.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from app.api import personal_leagues
from app.db.auth_models import AuthSessionPurpose, User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Season
from app.db.platform_enums import (
    SeasonSyncState,
    UserSeasonAccessStatus,
)
from app.db.platform_models import BackgroundJob, Installation, ProviderConnection
from app.db.session import SessionFactory
from tests.support.league_import import (  # noqa: F401 - fixtures
    SECRETS,
    Caller,
    FakeEspn,
    Registry,
    create_caller,
    forbid_live_network,
    league_id,
    private,
    registry,
    run_import,
)
from tests.support.league_season_access import (  # noqa: F401 - fixtures
    SeededSeason,
    get,
    grant,
    put_default,
    real_workspace_authentication,
    season_paths,
    seed_season,
    set_default,
    stored_default,
)
from tests.test_league_import_critical import run_threads, wait_for_lock_waiter

ACTIVE = UserSeasonAccessStatus.ACTIVE
REVERIFY = UserSeasonAccessStatus.REVERIFICATION_REQUIRED
REVOKED = UserSeasonAccessStatus.REVOKED
NOT_FOUND = {"detail": "League season not found."}
UNAVAILABLE = {"detail": "League season data is not available yet."}
SUMMARY_KEYS = {
    "season_id",
    "season_year",
    "access_status",
    "sync_state",
    "data_available",
    "last_successful_sync_at",
    "provider",
    "provider_league_id",
    "league_name",
}


def outcome(response) -> tuple[int, Any, str | None]:
    return response.status_code, response.json(), response.headers.get("cache-control")


def nonexistent_season_id() -> int:
    with SessionFactory() as session:
        return (session.scalar(select(Season.id).order_by(Season.id.desc()).limit(1)) or 0) + 10_000


def as_nonexistent(season: SeededSeason) -> SeededSeason:
    return SeededSeason(
        season_id=nonexistent_season_id(),
        league_pk=0,
        provider_league_id="0",
        year=season.year,
        team_ids=season.team_ids,
        marker="none",
    )


def assert_uniform_not_found(caller: Caller, season: SeededSeason) -> None:
    """Every personal route for ``season`` answers exactly like a nonexistent Season."""

    missing = as_nonexistent(season)

    for (path, params), (missing_path, missing_params) in zip(
        season_paths(season), season_paths(missing), strict=True
    ):
        hidden = get(caller, path, **params)
        absent = get(caller, missing_path, **missing_params)
        assert outcome(hidden) == outcome(absent) == (404, NOT_FOUND, "no-store"), path
        assert season.marker not in hidden.text


def personal_texts(caller: Caller, season: SeededSeason) -> dict[str, str]:
    return {path: get(caller, path, **params).text for path, params in season_paths(season)}


@pytest.fixture
def two_users(registry: Registry) -> tuple[Caller, Caller]:
    return create_caller(registry), create_caller(registry)


# --------------------------------------------------------------------------
# 1-5, 8: access states and uniform existence hiding
# --------------------------------------------------------------------------


def test_active_access_reads_every_personal_route(registry: Registry) -> None:
    user = create_caller(registry)
    season = seed_season(registry)
    grant(user.user_id, season.season_id)

    for path, params in season_paths(season):
        response = get(user, path, **params)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["cache-control"] == "no-store"

    snapshot = get(user, f"/api/seasons/{season.season_id}/snapshot").json()
    assert {team["name"] for team in snapshot["teams"]} == {
        f"{season.marker} Team 1",
        f"{season.marker} Team 2",
    }


def test_no_access_revoked_and_guessed_ids_are_indistinguishable_from_nonexistent(
    registry: Registry,
) -> None:
    owner, stranger, revoked = (create_caller(registry) for _ in range(3))
    season = seed_season(registry)
    neighbour = seed_season(registry)
    grant(owner.user_id, season.season_id)
    grant(owner.user_id, neighbour.season_id)
    grant(revoked.user_id, season.season_id, REVOKED)

    # A stranger, a revoked user, and sequential neighbouring IDs reveal nothing.
    assert_uniform_not_found(stranger, season)
    assert_uniform_not_found(stranger, neighbour)
    assert_uniform_not_found(revoked, season)

    for caller in (stranger, revoked):
        assert get(caller, "/api/leagues").json() == {"default_season_id": None, "leagues": []}


def test_reverification_required_with_prior_data_stays_readable_with_indicator(
    registry: Registry,
) -> None:
    user = create_caller(registry)
    season = seed_season(registry, sync_state=SeasonSyncState.STALE)
    grant(user.user_id, season.season_id, REVERIFY)

    for path, params in season_paths(season):
        assert get(user, path, **params).status_code == 200, path

    summary = get(user, f"/api/seasons/{season.season_id}").json()
    assert summary["access_status"] == "reverification_required"
    assert summary["data_available"] is True
    assert set(summary) == SUMMARY_KEYS


@pytest.mark.parametrize("sync_state", [SeasonSyncState.QUEUED, SeasonSyncState.FAILED])
def test_reverification_required_without_data_is_safe_and_fabricates_nothing(
    registry: Registry, sync_state: SeasonSyncState
) -> None:
    user = create_caller(registry)
    season = seed_season(registry, sync_state=sync_state, data=False)
    grant(user.user_id, season.season_id, REVERIFY)

    summary = get(user, f"/api/seasons/{season.season_id}").json()
    assert summary["access_status"] == "reverification_required"
    assert summary["data_available"] is False
    assert summary["sync_state"] == sync_state.value

    for path, params in season_paths(season)[1:]:
        assert outcome(get(user, path, **params)) == (409, UNAVAILABLE, "no-store"), path


# --------------------------------------------------------------------------
# 15-17: synchronization state and data availability
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sync_state", "data", "readable"),
    [
        (SeasonSyncState.FRESH, True, True),
        (SeasonSyncState.STALE, True, True),
        (SeasonSyncState.QUEUED, True, True),
        (SeasonSyncState.SYNCING, True, True),
        (SeasonSyncState.FAILED, True, True),
        (SeasonSyncState.QUEUED, False, False),
        (SeasonSyncState.SYNCING, False, False),
        (SeasonSyncState.FAILED, False, False),
        (SeasonSyncState.NEVER_SYNCED, False, False),
        (None, False, False),
    ],
)
def test_sync_state_controls_data_without_fabrication(
    registry: Registry, sync_state: SeasonSyncState | None, data: bool, readable: bool
) -> None:
    user = create_caller(registry)
    season = seed_season(registry, sync_state=sync_state, data=data)
    grant(user.user_id, season.season_id)

    summary = get(user, f"/api/seasons/{season.season_id}")
    assert summary.status_code == 200
    assert summary.json()["sync_state"] == (sync_state.value if sync_state else None)
    assert summary.json()["data_available"] is readable

    for path, params in season_paths(season)[1:]:
        response = get(user, path, **params)
        if readable:
            assert response.status_code == 200, path
        else:
            assert outcome(response) == (409, UNAVAILABLE, "no-store"), path


def test_rows_without_a_recorded_successful_sync_are_not_served(registry: Registry) -> None:
    """Stored rows alone never prove data availability (legacy or partial state)."""

    user = create_caller(registry)
    season = seed_season(registry, sync_state=SeasonSyncState.SYNCING)
    with SessionFactory() as session:
        session.execute(
            update(Season)
            .where(Season.id == season.season_id)
            .values(data_version=0, last_successful_sync_at=None, last_synced_at=None)
        )
        session.commit()
    grant(user.user_id, season.season_id)

    response = get(user, f"/api/seasons/{season.season_id}/snapshot")
    assert outcome(response) == (409, UNAVAILABLE, "no-store")
    assert season.marker not in response.text


# --------------------------------------------------------------------------
# 6, 7, 20, 22: workspace and personal authority stay separate
# --------------------------------------------------------------------------


def test_personal_access_does_not_authorize_workspace_routes(registry: Registry) -> None:
    owner = create_caller(registry)
    personal = create_caller(registry)  # own workspace, no claim
    season = seed_season(registry, workspace_id=owner.workspace_id)
    grant(personal.user_id, season.season_id)

    for path in ("/api/leagues/current", "/api/analytics/all-play"):
        response = get(personal, path, season_id=season.season_id)
        assert response.status_code != 200, path
        assert season.marker not in response.text
    # The workspace Season list shows only the caller's own (empty) workspace.
    seasons = get(personal, "/api/leagues/seasons")
    assert seasons.status_code != 200 or seasons.json() == {"seasons": []}

    # The claiming workspace still reads it through the workspace route.
    assert get(owner, "/api/leagues/current", season_id=season.season_id).status_code == 200


def test_workspace_claim_does_not_authorize_personal_routes(registry: Registry) -> None:
    admin = create_caller(registry)
    spectator = create_caller(registry, role=UserRole.SPECTATOR, workspace_id=admin.workspace_id)
    season = seed_season(registry, workspace_id=admin.workspace_id)

    workspace = get(admin, "/api/leagues/current", season_id=season.season_id)
    assert workspace.status_code == 200
    assert get(spectator, "/api/leagues/current", season_id=season.season_id).status_code == 200

    assert_uniform_not_found(admin, season)
    assert get(admin, "/api/leagues").json() == {"default_season_id": None, "leagues": []}


def test_workspace_and_personal_paths_return_the_same_canonical_data(registry: Registry) -> None:
    admin = create_caller(registry)
    season = seed_season(registry, workspace_id=admin.workspace_id)
    other_league = seed_season(registry)
    grant(admin.user_id, season.season_id)

    workspace = get(admin, "/api/leagues/current", season_id=season.season_id).json()
    personal = get(admin, f"/api/seasons/{season.season_id}/snapshot").json()
    assert workspace == personal

    workspace_all_play = get(admin, "/api/analytics/all-play", season_id=season.season_id)
    personal_all_play = get(admin, f"/api/seasons/{season.season_id}/analytics/all-play")
    assert workspace_all_play.json() == personal_all_play.json()

    # Neither path broadens the other.
    assert_uniform_not_found(admin, other_league)
    assert get(admin, "/api/leagues/current", season_id=other_league.season_id).status_code != 200


def test_cross_workspace_and_cross_user_access_never_crosses(registry: Registry) -> None:
    first_admin, second_admin = create_caller(registry), create_caller(registry)
    first = seed_season(registry, workspace_id=first_admin.workspace_id)
    second = seed_season(registry, workspace_id=second_admin.workspace_id)
    grant(first_admin.user_id, second.season_id)  # personal access across workspaces

    assert get(first_admin, f"/api/seasons/{second.season_id}/snapshot").status_code == 200
    assert_uniform_not_found(first_admin, first)  # own workspace claim is not personal access
    assert_uniform_not_found(second_admin, second)
    assert_uniform_not_found(second_admin, first)
    assert get(second_admin, "/api/leagues/current", season_id=first.season_id).status_code != 200


# --------------------------------------------------------------------------
# 9, 10: Season scoping and two users on one Season
# --------------------------------------------------------------------------


def test_authorized_season_cannot_reach_another_seasons_rows(registry: Registry) -> None:
    user = create_caller(registry)
    mine = seed_season(registry, year=2024)
    same_league_other_year = seed_season(
        registry, provider_league_id=mine.provider_league_id, year=2023
    )
    other_league = seed_season(registry, year=2024)
    grant(user.user_id, mine.season_id)

    base = f"/api/seasons/{mine.season_id}"
    texts = personal_texts(user, mine)
    for text in texts.values():
        assert same_league_other_year.marker not in text
        assert other_league.marker not in text

    predictor = get(user, f"{base}/playoff-predictor").json()
    assert {team["team_id"] for team in predictor["teams"]} == set(mine.team_ids)
    assert {
        team_id
        for matchup in predictor["completed_matchups"] + predictor["remaining_matchups"]
        for team_id in (matchup["home_team_id"], matchup["away_team_id"])
    } == set(mine.team_ids)
    lineups = get(user, f"{base}/analytics/optimal-lineups").json()
    assert {summary["team_id"] for summary in lineups} == set(mine.team_ids)

    absent_team = max(other_league.team_ids) + 10_000
    for foreign in (same_league_other_year, other_league):
        foreign_team = foreign.team_ids[0]
        # Another Season's team answers exactly like a team that does not exist.
        swap = get(
            user,
            f"{base}/analytics/schedule-swap",
            first_team_id=mine.team_ids[0],
            second_team_id=foreign_team,
        )
        swap_absent = get(
            user,
            f"{base}/analytics/schedule-swap",
            first_team_id=mine.team_ids[0],
            second_team_id=absent_team,
        )
        assert swap.status_code == swap_absent.status_code == 422
        assert swap.json()["detail"].replace(str(foreign_team), "X") == swap_absent.json()[
            "detail"
        ].replace(str(absent_team), "X")

        lineup = get(user, f"{base}/analytics/optimal-lineups/{foreign_team}")
        lineup_absent = get(user, f"{base}/analytics/optimal-lineups/{absent_team}")
        assert lineup.status_code == lineup_absent.status_code == 404
        assert foreign.marker not in lineup.text

        assert_uniform_not_found(user, foreign)


def test_two_users_share_canonical_data_without_seeing_each_other(registry: Registry) -> None:
    first, second = create_caller(registry), create_caller(registry)
    season = seed_season(registry)
    grant(first.user_id, season.season_id)
    grant(second.user_id, season.season_id, REVERIFY)

    first_texts = personal_texts(first, season)
    second_texts = personal_texts(second, season)
    summary_path = f"/api/seasons/{season.season_id}"

    for path in first_texts:
        if path != summary_path:
            assert first_texts[path] == second_texts[path], path

    # Each sees only their own access state.
    assert get(first, summary_path).json()["access_status"] == "active"
    assert get(second, summary_path).json()["access_status"] == "reverification_required"

    with SessionFactory() as session:
        other = session.get(User, second.user_id)
        assert other is not None
        identifying = {other.username, other.email, str(second.workspace_id)}
        slug = session.scalar(
            select(Installation.slug).where(Installation.id == second.workspace_id)
        )

    for text in [*first_texts.values(), get(first, "/api/leagues").text]:
        for value in (*identifying, slug):
            assert f'"{value}"' not in text


# --------------------------------------------------------------------------
# 11, 23: default preference
# --------------------------------------------------------------------------


def test_default_preference_never_bypasses_access(registry: Registry) -> None:
    user, other = create_caller(registry), create_caller(registry)
    readable = seed_season(registry)
    foreign = seed_season(registry)
    revoked = seed_season(registry)
    grant(user.user_id, readable.season_id)
    grant(user.user_id, revoked.season_id, REVOKED)
    grant(other.user_id, foreign.season_id)

    for stale in (foreign.season_id, revoked.season_id):
        set_default(user.user_id, stale)
        listing = get(user, "/api/leagues").json()
        assert listing["default_season_id"] is None  # ignored, not followed
        assert stale not in {s["season_id"] for lg in listing["leagues"] for s in lg["seasons"]}
        assert_uniform_not_found(user, foreign if stale == foreign.season_id else revoked)
        assert stored_default(user.user_id) == stale  # reads never write

        response = put_default(user, {"season_id": stale})
        assert outcome(response) == (404, NOT_FOUND, "no-store")
        assert stored_default(user.user_id) == stale

    assert outcome(put_default(user, {"season_id": nonexistent_season_id()}))[:2] == (
        404,
        NOT_FOUND,
    )

    response = put_default(user, {"season_id": readable.season_id})
    assert response.status_code == 200
    assert response.json() == {"default_season_id": readable.season_id}
    assert get(user, "/api/leagues").json()["default_season_id"] == readable.season_id


def test_default_preference_mutation_enforces_csrf_origin_and_ownership(
    registry: Registry,
) -> None:
    user, other = create_caller(registry), create_caller(registry)
    season = seed_season(registry)
    others = seed_season(registry)
    grant(user.user_id, season.season_id)
    grant(other.user_id, season.season_id)
    grant(other.user_id, others.season_id)
    set_default(other.user_id, others.season_id)

    assert put_default(None, {"season_id": season.season_id}).status_code == 401
    assert put_default(user, {"season_id": season.season_id}, csrf=False).status_code == 403
    assert (
        put_default(user, {"season_id": season.season_id}, origin="https://evil.example")
    ).status_code == 403
    assert (
        put_default(user, {"season_id": season.season_id, "user_id": other.user_id})
    ).status_code == 422
    assert stored_default(user.user_id) is None

    assert put_default(user, {"season_id": season.season_id}).status_code == 200
    assert stored_default(user.user_id) == season.season_id
    assert stored_default(other.user_id) == others.season_id  # never another user's


# --------------------------------------------------------------------------
# 12, 13, 24: revocation and deletion races
# --------------------------------------------------------------------------


@pytest.mark.parametrize("change", ["revoke", "delete", "rollback"])
def test_read_waiting_on_a_concurrent_access_change_uses_the_committed_outcome(
    registry: Registry, change: str
) -> None:
    user = create_caller(registry)
    season = seed_season(registry)
    grant(user.user_id, season.season_id)
    access = (
        UserLeagueSeasonAccess.user_id == user.user_id,
        UserLeagueSeasonAccess.season_id == season.season_id,
    )

    with SessionFactory() as writer:
        if change == "delete":
            writer.execute(delete(UserLeagueSeasonAccess).where(*access))
        else:
            writer.execute(update(UserLeagueSeasonAccess).where(*access).values(status=REVOKED))

        reader: dict[str, Any] = {}
        thread = threading.Thread(
            target=lambda: reader.setdefault(
                "response", get(user, f"/api/seasons/{season.season_id}/snapshot")
            )
        )
        thread.start()
        wait_for_lock_waiter()  # the read is blocked on the uncommitted change
        if change == "rollback":
            writer.rollback()
        else:
            writer.commit()
        thread.join(timeout=30)
        assert not thread.is_alive()

    response = reader["response"]
    if change == "rollback":
        assert response.status_code == 200
        assert season.marker in response.text
    else:
        assert outcome(response) == (404, NOT_FOUND, "no-store")
        assert_uniform_not_found(user, season)  # and every later read


def test_read_in_progress_holds_authority_until_it_completes(
    registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_caller(registry)
    season = seed_season(registry)
    grant(user.user_id, season.season_id)
    authorized = threading.Event()
    release = threading.Event()
    load = personal_leagues.load_league_snapshot

    def paused_load(session, *, season_id: int):
        authorized.set()
        assert release.wait(30)
        return load(session, season_id=season_id)

    monkeypatch.setattr(personal_leagues, "load_league_snapshot", paused_load)

    def read():
        return get(user, f"/api/seasons/{season.season_id}/snapshot")

    def revoke() -> str:
        assert authorized.wait(30)
        with SessionFactory() as session:
            session.execute(
                update(UserLeagueSeasonAccess)
                .where(
                    UserLeagueSeasonAccess.user_id == user.user_id,
                    UserLeagueSeasonAccess.season_id == season.season_id,
                )
                .values(status=REVOKED)
            )
            session.commit()
        return "revoked"

    def coordinate() -> None:
        assert authorized.wait(30)
        wait_for_lock_waiter()  # revocation waits for the authorized read
        release.set()

    response, revoked, _ = run_threads(read, revoke, coordinate)

    assert revoked == "revoked"
    assert response.status_code == 200  # authorized before revocation committed
    assert season.marker in response.text
    monkeypatch.setattr(personal_leagues, "load_league_snapshot", load)
    assert_uniform_not_found(user, season)


def test_deleting_the_access_row_removes_future_read_authority(registry: Registry) -> None:
    user = create_caller(registry)
    season = seed_season(registry)
    grant(user.user_id, season.season_id)
    set_default(user.user_id, season.season_id)
    assert get(user, f"/api/seasons/{season.season_id}/snapshot").status_code == 200

    with SessionFactory() as session:
        session.execute(
            delete(UserLeagueSeasonAccess).where(UserLeagueSeasonAccess.user_id == user.user_id)
        )
        session.commit()

    assert_uniform_not_found(user, season)
    assert get(user, "/api/leagues").json() == {"default_season_id": None, "leagues": []}


# --------------------------------------------------------------------------
# 14: orphaned League with active access
# --------------------------------------------------------------------------


def test_orphaned_league_with_active_access_is_readable_and_unchanged_by_reads(
    registry: Registry,
) -> None:
    user = create_caller(registry)
    season = seed_season(registry)
    grant(user.user_id, season.season_id)
    orphaned_at = datetime(2026, 9, 1, tzinfo=UTC)
    with SessionFactory() as session:
        session.execute(
            update(League).where(League.id == season.league_pk).values(orphaned_at=orphaned_at)
        )
        session.commit()

    for path, params in season_paths(season):
        response = get(user, path, **params)
        assert response.status_code == 200, path
        assert "orphan" not in response.text
    assert get(user, "/api/leagues").json()["leagues"][0]["seasons"][0]["season_id"] == (
        season.season_id
    )

    with SessionFactory() as session:
        assert session.get(League, season.league_pk).orphaned_at == orphaned_at


# --------------------------------------------------------------------------
# 18, 19: no credential, owner, job, workspace or other-user detail
# --------------------------------------------------------------------------


def test_status_and_logs_reveal_no_owner_connection_job_or_workspace(
    registry: Registry, caplog: pytest.LogCaptureFixture
) -> None:
    reader, owner = create_caller(registry), create_caller(registry)
    season = seed_season(
        registry, workspace_id=owner.workspace_id, sync_state=SeasonSyncState.SYNCING
    )

    # The owner proves private access for real: own connection, access row, one job.
    run_import(owner, season.provider_league_id, FakeEspn(mode="private"), private_access=private())

    with SessionFactory() as session:
        connection = session.scalar(
            select(ProviderConnection).where(ProviderConnection.user_id == owner.user_id)
        )
        assert connection is not None
        connection.display_name = "Owner ESPN connection"
        connection.last_validation_error = "SECRET-PROVIDER-DETAIL"
        job_id = session.scalar(
            select(BackgroundJob.id).where(
                BackgroundJob.idempotency_key == f"league-season-sync:{season.season_id}"
            )
        )
        assert job_id is not None
        session.execute(
            update(Season)
            .where(Season.id == season.season_id)
            .values(last_sync_failure_code="provider_credentials_invalid")
        )
        session.commit()
        connection_id = connection.id
        owner_user = session.get(User, owner.user_id)
        owner_values = {owner_user.username, owner_user.email}
        slug = session.scalar(
            select(Installation.slug).where(Installation.id == owner.workspace_id)
        )

    grant(reader.user_id, season.season_id)

    with caplog.at_level(logging.DEBUG):
        summary = get(reader, f"/api/seasons/{season.season_id}")
        listing = get(reader, "/api/leagues")
        texts = [summary.text, listing.text, *personal_texts(reader, season).values()]

    assert set(summary.json()) == SUMMARY_KEYS
    assert set(listing.json()["leagues"][0]["seasons"][0]) == SUMMARY_KEYS - {
        "provider",
        "provider_league_id",
        "league_name",
    }
    forbidden = [
        *SECRETS,
        "SECRET-PROVIDER-DETAIL",
        "provider_credentials_invalid",
        "Owner ESPN connection",
        slug,
        *owner_values,
    ]
    for text in [*texts, caplog.text]:
        for value in forbidden:
            assert value not in text
    for key in ("connection", "job", "workspace", "owner", "user_id", "claim", "failure"):
        assert key not in summary.text
        assert key not in listing.text
    assert '"job_id"' not in summary.text and str(job_id) not in summary.json().values()
    assert connection_id not in summary.json().values()


# --------------------------------------------------------------------------
# Transitional identity boundary
# --------------------------------------------------------------------------


def test_identity_boundary_refuses_before_any_season_lookup(registry: Registry) -> None:
    owner = create_caller(registry)
    season = seed_season(registry)
    spectator = create_caller(registry, role=UserRole.SPECTATOR, verified=False)
    unverified_admin = create_caller(registry, verified=False)
    restricted = create_caller(registry, purpose=AuthSessionPurpose.DELETION_RECOVERY)
    inactive = create_caller(registry, active=False)
    for caller in (owner, spectator, unverified_admin, restricted, inactive):
        grant(caller.user_id, season.season_id)

    missing = nonexistent_season_id()
    for caller, status_code in (
        (spectator, 403),
        (unverified_admin, 403),
        (restricted, 403),
        (inactive, 401),
        (None, 401),
    ):
        for path in (f"/api/seasons/{season.season_id}", f"/api/seasons/{missing}", "/api/leagues"):
            response = get(caller, path)
            assert response.status_code == status_code, (path, response.text)
            assert season.marker not in response.text
        if caller is not None:
            existing = get(caller, f"/api/seasons/{season.season_id}")
            absent = get(caller, f"/api/seasons/{missing}")
            assert existing.json() == absent.json()

    # A verified administrator in a workspace without any claim reads by access alone.
    assert get(owner, f"/api/seasons/{season.season_id}/snapshot").status_code == 200


# --------------------------------------------------------------------------
# 21: League import response stays compatible with personal reads
# --------------------------------------------------------------------------


def test_imported_season_appears_in_personal_reads(registry: Registry, league_id: str) -> None:
    user = create_caller(registry)
    result = run_import(user, league_id, FakeEspn())

    listing = get(user, "/api/leagues").json()
    assert listing["default_season_id"] == result.season_id
    [league] = listing["leagues"]
    assert league["provider_league_id"] == result.provider_league_id
    assert league["league_name"] == result.league_name
    [season] = league["seasons"]
    assert season["season_id"] == result.season_id
    assert season["sync_state"] == result.sync_state.value == "queued"
    assert season["data_available"] is result.data_available is False

    # Draft Season status is irrelevant; no data yet means no fabricated analytics.
    snapshot = get(user, f"/api/seasons/{result.season_id}/snapshot")
    assert outcome(snapshot) == (409, UNAVAILABLE, "no-store")
