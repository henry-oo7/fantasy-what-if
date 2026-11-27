# ruff: noqa: F811  (imported pytest fixtures are used as test parameters)
"""Critical invariants of personal League import (ADR 0010).

Real PostgreSQL, committed data, deterministic concurrency through barriers,
events and observed lock waits. Fake ESPN transport only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.auth_models import AuthSessionPurpose, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess, UserPreference
from app.db.models import League, Season
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    ProviderAccessMode,
    SeasonSyncState,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import BackgroundJob, ProviderConnection, ProviderLeagueClaim
from app.db.session import SessionFactory, engine
from app.providers.credential_vault import ProviderCredentialVault
from app.repositories.league_access_repository import LeagueAccessRepository
from app.services import league_import as league_import_module
from app.services.league_import import VerifiedLeagueImport
from app.services.provider_onboarding import ProviderOnboardingError
from app.services.synchronization_job_contract import build_season_sync_payload
from tests.support.league_import import (  # noqa: F401 - fixtures
    CURRENT_WEEK,
    KEYRING,
    LEAGUE_NAME,
    OTHER_S2,
    REGULAR_SEASON_WEEKS,
    SEASON,
    SECRETS,
    SYNTHETIC_S2,
    WRONG_S2,
    Caller,
    FakeEspn,
    Registry,
    access_row,
    api_espn,
    checked_out_connections,
    create_caller,
    credentials_body,
    espn,
    forbid_live_network,
    import_body,
    import_state,
    league_id,
    post_import,
    preference,
    private,
    registry,
    run_import,
    unique_league_id,
    user_connections,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def run_threads(*functions: Callable[[], Any]) -> list[Any]:
    results: list[Any] = [None] * len(functions)

    def wrap(index: int, function: Callable[[], Any]) -> None:
        try:
            results[index] = function()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            results[index] = exc

    threads = [
        threading.Thread(target=wrap, args=(index, function))
        for index, function in enumerate(functions)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "worker thread did not finish"

    return results


def wait_for_lock_waiter(timeout: float = 10.0) -> None:
    """Observe (not sleep for) a backend blocked on a row or tuple lock."""

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        if waiting:
            return
        time.sleep(0.01)

    raise AssertionError("No blocked lock waiter was observed.")


def season_of(league_id: str, year: int = SEASON) -> Season:
    with SessionFactory() as session:
        season = session.scalar(
            select(Season)
            .join(League, League.id == Season.league_id)
            .where(
                League.provider == FantasyProvider.ESPN,
                League.provider_league_id == league_id,
                Season.year == year,
            )
        )
        assert season is not None
        session.expunge(season)
        return season


def league_of(league_id: str) -> League | None:
    with SessionFactory() as session:
        league = session.scalar(
            select(League).where(
                League.provider == FantasyProvider.ESPN, League.provider_league_id == league_id
            )
        )
        if league is not None:
            session.expunge(league)
        return league


def set_access_status(user_id: int, season_id: int, status: UserSeasonAccessStatus) -> None:
    with SessionFactory() as session:
        row = session.scalar(
            select(UserLeagueSeasonAccess).where(
                UserLeagueSeasonAccess.user_id == user_id,
                UserLeagueSeasonAccess.season_id == season_id,
            )
        )
        assert row is not None
        row.status = status
        session.commit()


def create_workspace_claim(registry: Registry, league_id: str) -> tuple[int, int, int]:
    """A second workspace that claims the League; returns (workspace, league, claim) IDs."""

    owner = create_caller(registry)

    with SessionFactory() as session:
        league = League(
            name="Workspace League Name",
            provider=FantasyProvider.ESPN,
            provider_league_id=league_id,
            installation_id=owner.workspace_id,
        )
        session.add(league)
        session.flush()
        claim = ProviderLeagueClaim(
            workspace_id=owner.workspace_id,
            league_id=league.id,
            provider=FantasyProvider.ESPN,
            provider_league_id=league_id,
            normalized_provider_league_id=league_id,
        )
        session.add(claim)
        session.commit()
        return owner.workspace_id, league.id, claim.id


def claim_snapshot(claim_id: int) -> tuple[Any, ...]:
    with SessionFactory() as session:
        claim = session.get(ProviderLeagueClaim, claim_id)
        assert claim is not None
        return (
            claim.workspace_id,
            claim.league_id,
            claim.provider,
            claim.normalized_provider_league_id,
            claim.updated_at,
        )


def claim_count() -> int:
    with SessionFactory() as session:
        return int(session.scalar(select(func.count()).select_from(ProviderLeagueClaim)) or 0)


def fail_import(caller: Caller, league_id: str, espn: FakeEspn, **kwargs: Any) -> str:
    with pytest.raises(ProviderOnboardingError) as raised:
        run_import(caller, league_id, espn, **kwargs)
    return raised.value.code


# --------------------------------------------------------------------------
# 1, 2, 24: creation, reuse, claim independence
# --------------------------------------------------------------------------


def test_first_import_creates_league_season_access_and_one_job(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    caller = create_caller(registry)
    claims_before = claim_count()

    result = run_import(caller, league_id, espn)

    assert import_state(league_id) == import_state(league_id).__class__(
        leagues=1, seasons=1, access=1, connections=0, active_jobs=1
    )
    season = season_of(league_id)
    league = league_of(league_id)
    assert league is not None
    assert league.name == LEAGUE_NAME and league.installation_id is None
    assert league.orphaned_at is None
    assert (season.current_week, season.regular_season_weeks) == (
        CURRENT_WEEK,
        REGULAR_SEASON_WEEKS,
    )
    assert season.provider_season_id == str(SEASON)
    assert season.sync_state is SeasonSyncState.QUEUED
    assert result.season_id == season.id and result.sync_queued and not result.data_available
    access = access_row(caller.user_id, season.id)
    assert access is not None and access.status is UserSeasonAccessStatus.ACTIVE
    assert access.proof_mode is UserSeasonProofMode.PUBLIC
    assert claim_count() == claims_before  # import never creates a claim

    with SessionFactory() as session:
        job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.idempotency_key == f"league-season-sync:{season.id}"
            )
        )
        assert job is not None and job.workspace_id is None
        assert job.payload == build_season_sync_payload(season.id)  # Season only, no owner


def test_second_user_reuses_league_and_season_with_independent_access(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    first = create_caller(registry)
    second = create_caller(registry)

    first_result = run_import(first, league_id, espn)
    second_result = run_import(second, league_id, espn)

    assert second_result.season_id == first_result.season_id
    state = import_state(league_id)
    assert (state.leagues, state.seasons, state.access, state.active_jobs) == (1, 1, 2, 1)
    assert access_row(first.user_id, first_result.season_id) is not None
    assert access_row(second.user_id, first_result.season_id) is not None


def test_workspace_claim_stays_unchanged_hidden_and_never_authorizes(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    workspace_id, claimed_league_id, claim_id = create_workspace_claim(registry, league_id)
    before = claim_snapshot(claim_id)
    caller = create_caller(registry)

    # The claim does not block an independent successful proof.
    result = run_import(caller, league_id, espn)

    league = league_of(league_id)
    assert league is not None and league.id == claimed_league_id
    assert league.installation_id == workspace_id  # compatibility metadata untouched
    assert league.name == "Workspace League Name"  # existing canonical name kept
    assert claim_snapshot(claim_id) == before
    assert result.league_name == "Workspace League Name"

    # The claim never authorizes: an administrator of the claiming workspace
    # still needs their own successful proof.
    with SessionFactory() as session:
        owner_admin_id = session.scalar(
            text("SELECT id FROM users WHERE workspace_id = :w AND role = 'admin'"),
            {"w": workspace_id},
        )
    with SessionFactory() as session:
        owner = Caller(
            owner_admin_id,
            session.scalar(
                text("SELECT id FROM auth_sessions WHERE user_id = :u"), {"u": owner_admin_id}
            ),
            "",
            "",
            workspace_id,
        )
    espn.mode = "missing"
    assert fail_import(owner, league_id, espn) == "provider_validation_failed"
    assert access_row(owner.user_id, result.season_id) is None
    assert claim_snapshot(claim_id) == before


def test_api_response_hides_claim_owner_workspace_and_internal_ids(
    registry: Registry, league_id: str, api_espn: FakeEspn
) -> None:
    workspace_id, _, _ = create_workspace_claim(registry, league_id)
    caller = create_caller(registry)

    response = post_import(caller, import_body(league_id, credentials=credentials_body()))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "provider",
        "provider_league_id",
        "league_name",
        "season_id",
        "season_year",
        "access_status",
        "sync_state",
        "data_available",
        "sync_queued",
    }
    raw = response.text
    for forbidden in ("workspace", "user_id", "connection", "claim", "job", "owner"):
        assert forbidden not in raw
    assert str(workspace_id) not in json.dumps({k: v for k, v in body.items() if k != "season_id"})


# --------------------------------------------------------------------------
# 3, 4, 5: proof is independent, failure stores nothing and hides existence
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "credentials", "code"),
    [
        ("missing", None, "provider_validation_failed"),
        ("private", None, "provider_credentials_required"),
        ("private", WRONG_S2, "provider_authentication_failed"),
        ("error500", None, "provider_unavailable"),
    ],
)
def test_existing_canonical_data_never_substitutes_for_proof(
    registry: Registry,
    league_id: str,
    espn: FakeEspn,
    mode: str,
    credentials: str | None,
    code: str,
) -> None:
    first = create_caller(registry)
    run_import(first, league_id, espn, private_access=private())
    newcomer = create_caller(registry)
    before = import_state(league_id, user_ids=(newcomer.user_id,))

    espn.mode = mode
    espn.valid_s2 = {SYNTHETIC_S2}
    assert (
        fail_import(
            newcomer,
            league_id,
            espn,
            private_access=private(credentials) if credentials else None,
        )
        == code
    )

    assert import_state(league_id, user_ids=(newcomer.user_id,)) == before
    assert access_row(newcomer.user_id, season_of(league_id).id) is None
    assert user_connections(newcomer.user_id) == []
    # Proof requests never carried the first user's stored credentials.
    for call in espn.calls[-2:]:
        cookies = call.get("cookies") or {}
        assert cookies.get("espn_s2") in (None, credentials)


def test_failed_private_proof_persists_no_import_state(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    caller = create_caller(registry)
    espn.mode = "private"

    assert (
        fail_import(caller, league_id, espn, private_access=private(WRONG_S2))
        == "provider_authentication_failed"
    )

    assert import_state(league_id, user_ids=(caller.user_id,)) == import_state(unique_league_id())
    assert user_connections(caller.user_id) == []
    assert preference(caller.user_id) is None


@pytest.mark.parametrize(
    ("mode", "credentials"),
    [("missing", None), ("private", None), ("private", WRONG_S2), ("timeout", None)],
)
def test_failure_response_is_identical_whether_or_not_league_exists(
    registry: Registry, api_espn: FakeEspn, mode: str, credentials: str | None
) -> None:
    existing_id, absent_id = unique_league_id(), unique_league_id()
    registry.league_ids.extend([existing_id, absent_id])
    run_import(create_caller(registry), existing_id, api_espn, private_access=private())
    caller = create_caller(registry)
    api_espn.mode = mode
    api_espn.valid_s2 = {SYNTHETIC_S2}
    extra = {"credentials": credentials_body(credentials)} if credentials else {}

    existing = post_import(caller, import_body(existing_id, **extra))
    absent = post_import(caller, import_body(absent_id, **extra))

    assert existing.status_code == absent.status_code
    assert existing.json() == absent.json()
    assert set(existing.json()) == {"detail", "code", "retryable"}


# --------------------------------------------------------------------------
# 6, 7, 8: idempotency and concurrency
# --------------------------------------------------------------------------


def test_same_user_repeat_is_idempotent(registry: Registry, league_id: str, espn: FakeEspn) -> None:
    caller = create_caller(registry)
    espn.mode = "private"

    first = run_import(caller, league_id, espn, private_access=private())
    connection_before = user_connections(caller.user_id)
    second = run_import(caller, league_id, espn, private_access=private())

    assert second.season_id == first.season_id
    assert import_state(league_id, user_ids=(caller.user_id,)) == import_state(
        league_id, user_ids=(caller.user_id,)
    ).__class__(leagues=1, seasons=1, access=1, connections=1, active_jobs=1)
    connection_after = user_connections(caller.user_id)
    assert [c.id for c in connection_after] == [c.id for c in connection_before]
    assert preference(caller.user_id) == first.season_id


def test_two_users_concurrent_first_import_creates_one_league_season_and_job(
    registry: Registry, league_id: str, espn: FakeEspn, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = create_caller(registry), create_caller(registry)
    espn.mode = "private"
    original = LeagueAccessRepository.lock_or_create_league
    barrier = threading.Barrier(2, timeout=10)

    def contended(self: LeagueAccessRepository, **kwargs: Any) -> League:
        barrier.wait()  # both transactions insert the new identity together
        return original(self, **kwargs)

    monkeypatch.setattr(LeagueAccessRepository, "lock_or_create_league", contended)

    results = run_threads(
        lambda: run_import(first, league_id, espn, private_access=private(SYNTHETIC_S2)),
        lambda: run_import(second, league_id, espn, private_access=private(OTHER_S2)),
    )

    assert not any(isinstance(result, BaseException) for result in results), results
    assert results[0].season_id == results[1].season_id
    state = import_state(league_id, user_ids=(first.user_id, second.user_id))
    assert state == state.__class__(leagues=1, seasons=1, access=2, connections=2, active_jobs=1)
    first_access = access_row(first.user_id, results[0].season_id)
    second_access = access_row(second.user_id, results[0].season_id)
    assert first_access is not None and second_access is not None
    assert first_access.provider_connection_id == user_connections(first.user_id)[0].id
    assert second_access.provider_connection_id == user_connections(second.user_id)[0].id


def test_same_user_concurrent_imports_stay_bounded(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    caller = create_caller(registry)
    other = create_caller(registry)
    # The user's existing default must never be overwritten.
    other_league = unique_league_id()
    registry.league_ids.append(other_league)
    default = run_import(caller, other_league, espn).season_id
    espn.mode = "private"
    barrier = threading.Barrier(2, timeout=10)
    espn.before_respond = lambda: barrier.wait()  # both proofs finish together

    results = run_threads(
        lambda: run_import(caller, league_id, espn, private_access=private()),
        lambda: run_import(caller, league_id, espn, private_access=private()),
    )

    assert not any(isinstance(result, BaseException) for result in results), results
    state = import_state(league_id, user_ids=(caller.user_id,))
    assert state == state.__class__(leagues=1, seasons=1, access=1, connections=1, active_jobs=1)
    assert preference(caller.user_id) == default
    assert user_connections(other.user_id) == []


# --------------------------------------------------------------------------
# 9, 10, 11: connection ownership
# --------------------------------------------------------------------------


def test_private_access_references_only_the_requesting_users_connection(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    first, second = create_caller(registry), create_caller(registry)
    espn.mode = "private"

    # Identical credentials still yield independent, user-owned connections.
    result = run_import(first, league_id, espn, private_access=private())
    run_import(second, league_id, espn, private_access=private())

    for caller in (first, second):
        connections = user_connections(caller.user_id)
        assert len(connections) == 1
        connection = connections[0]
        assert connection.user_id == caller.user_id and connection.installation_id is None
        assert connection.provider is FantasyProvider.ESPN
        assert connection.access_mode is ProviderAccessMode.PRIVATE
        assert connection.provider_league_id == league_id
        access = access_row(caller.user_id, result.season_id)
        assert access is not None and access.provider_connection_id == connection.id
        assert access.proof_mode is UserSeasonProofMode.PRIVATE_CREDENTIALS

    assert user_connections(first.user_id)[0].id != user_connections(second.user_id)[0].id


def test_access_cannot_reference_another_users_connection(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    owner, intruder = create_caller(registry), create_caller(registry)
    espn.mode = "private"
    run_import(owner, league_id, espn, private_access=private())
    foreign = user_connections(owner.user_id)[0]
    verified = VerifiedLeagueImport(
        provider=FantasyProvider.ESPN,
        provider_league_id=league_id,
        season_year=SEASON,
        league_name=LEAGUE_NAME,
        current_week=CURRENT_WEEK,
        regular_season_weeks=REGULAR_SEASON_WEEKS,
        proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
        private_access=private(),
    )

    with SessionFactory() as session, pytest.raises(ValueError):
        league_import_module._activate_access(
            session,
            LeagueAccessRepository(session),
            user_id=intruder.user_id,
            season_id=season_of(league_id).id,
            verified=verified,
            connection=session.get(ProviderConnection, foreign.id),
            now=datetime.now(UTC),
        )

    assert access_row(intruder.user_id, season_of(league_id).id) is None


def test_api_rejects_client_supplied_connection_or_ownership_fields(
    registry: Registry, league_id: str, api_espn: FakeEspn
) -> None:
    caller = create_caller(registry)

    for field in ("provider_connection_id", "user_id", "workspace_id", "season_id", "proof_mode"):
        response = post_import(caller, import_body(league_id, **{field: 1}))
        assert response.status_code == 422

    assert api_espn.calls == []
    assert import_state(league_id).leagues == 0


def test_public_proof_creates_no_credentials_and_clears_access_connection(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    caller = create_caller(registry)

    # Public: submitted credentials are discarded unused.
    result = run_import(caller, league_id, espn, private_access=private())
    assert user_connections(caller.user_id) == []
    access = access_row(caller.user_id, result.season_id)
    assert access is not None and access.provider_connection_id is None
    assert access.proof_mode is UserSeasonProofMode.PUBLIC

    # Private, then public again: the reference is dropped, the row kept.
    espn.mode = "private"
    run_import(caller, league_id, espn, private_access=private())
    connection = user_connections(caller.user_id)[0]
    espn.mode = "public"
    run_import(caller, league_id, espn)

    access = access_row(caller.user_id, result.season_id)
    assert access is not None and access.provider_connection_id is None
    assert access.proof_mode is UserSeasonProofMode.PUBLIC
    assert [c.id for c in user_connections(caller.user_id)] == [connection.id]


# --------------------------------------------------------------------------
# 12: reactivation only after proof
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", [UserSeasonAccessStatus.REVERIFICATION_REQUIRED, UserSeasonAccessStatus.REVOKED]
)
def test_inactive_access_reactivates_only_after_successful_proof(
    registry: Registry, league_id: str, espn: FakeEspn, status: UserSeasonAccessStatus
) -> None:
    caller = create_caller(registry)
    season_id = run_import(caller, league_id, espn).season_id
    set_access_status(caller.user_id, season_id, status)

    espn.mode = "private"
    assert fail_import(caller, league_id, espn) == "provider_credentials_required"
    assert fail_import(caller, league_id, espn, private_access=private(WRONG_S2)) == (
        "provider_authentication_failed"
    )
    unchanged = access_row(caller.user_id, season_id)
    assert unchanged is not None and unchanged.status is status

    run_import(caller, league_id, espn, private_access=private())

    access = access_row(caller.user_id, season_id)
    assert access is not None and access.status is UserSeasonAccessStatus.ACTIVE
    assert access.proof_mode is UserSeasonProofMode.PRIVATE_CREDENTIALS
    assert access.provider_connection_id == user_connections(caller.user_id)[0].id
    assert access.last_reverified_at is not None
    assert import_state(league_id).access == 1


# --------------------------------------------------------------------------
# 13: orphan revival against a synthetic purge
# --------------------------------------------------------------------------


def synthetic_purge(league_pk: int, *, locked: threading.Event | None = None) -> bool:
    """Lock the League, then delete it only if orphaned with zero active access."""

    with Session(engine) as session:
        league = session.scalar(select(League).where(League.id == league_pk).with_for_update())
        if locked is not None:
            locked.set()
        if league is None or league.orphaned_at is None:
            session.commit()
            return False
        active = session.scalar(
            select(func.count())
            .select_from(UserLeagueSeasonAccess)
            .join(Season, Season.id == UserLeagueSeasonAccess.season_id)
            .where(
                Season.league_id == league_pk,
                UserLeagueSeasonAccess.status == UserSeasonAccessStatus.ACTIVE,
            )
        )
        if active:
            session.commit()
            return False
        session.delete(league)
        session.commit()
        return True


def orphan(caller: Caller, league_id: str, espn: FakeEspn) -> tuple[int, int]:
    season_id = run_import(caller, league_id, espn).season_id
    set_access_status(caller.user_id, season_id, UserSeasonAccessStatus.REVOKED)
    league = league_of(league_id)
    assert league is not None
    with SessionFactory() as session:
        assert LeagueAccessRepository(session).mark_league_orphaned(
            league_id=league.id, orphaned_at=datetime.now(UTC) - timedelta(days=31)
        )
        session.commit()
    return league.id, season_id


def test_revival_holding_league_lock_defeats_competing_purge(
    registry: Registry, league_id: str, espn: FakeEspn, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = create_caller(registry)
    league_pk, season_id = orphan(caller, league_id, espn)
    original = LeagueAccessRepository.lock_or_create_league
    locked, release = threading.Event(), threading.Event()

    def pause_after_lock(self: LeagueAccessRepository, **kwargs: Any) -> League:
        league = original(self, **kwargs)
        locked.set()
        assert release.wait(10)
        return league

    monkeypatch.setattr(LeagueAccessRepository, "lock_or_create_league", pause_after_lock)
    outcome: dict[str, Any] = {}
    importer = threading.Thread(
        target=lambda: outcome.update(r=run_import(caller, league_id, espn))
    )
    importer.start()
    assert locked.wait(10)
    purge = threading.Thread(target=lambda: outcome.update(purged=synthetic_purge(league_pk)))
    purge.start()
    wait_for_lock_waiter()  # purge is blocked behind the import's League lock
    release.set()
    importer.join(10)
    purge.join(10)

    assert outcome["purged"] is False
    league = league_of(league_id)
    assert league is not None and league.id == league_pk and league.orphaned_at is None
    access = access_row(caller.user_id, season_id)
    assert access is not None and access.status is UserSeasonAccessStatus.ACTIVE


def test_import_waiting_on_purge_recreates_the_purged_league(
    registry: Registry, league_id: str, espn: FakeEspn, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = create_caller(registry)
    league_pk, purged_season_id = orphan(caller, league_id, espn)
    registry.season_ids.append(purged_season_id)
    purge_locked, purge_release = threading.Event(), threading.Event()
    outcome: dict[str, Any] = {}

    def purge_holding_lock() -> None:
        with Session(engine) as session:
            league = session.scalar(select(League).where(League.id == league_pk).with_for_update())
            purge_locked.set()
            assert purge_release.wait(10)
            session.delete(league)
            session.commit()

    purge = threading.Thread(target=purge_holding_lock)
    purge.start()
    assert purge_locked.wait(10)
    importer = threading.Thread(
        target=lambda: outcome.update(r=run_import(caller, league_id, espn))
    )
    importer.start()
    wait_for_lock_waiter()  # the import waits on the purge's League lock
    purge_release.set()
    purge.join(10)
    importer.join(10)

    result = outcome["r"]
    league = league_of(league_id)
    assert league is not None and league.id != league_pk and league.orphaned_at is None
    access = access_row(caller.user_id, result.season_id)
    assert access is not None and access.status is UserSeasonAccessStatus.ACTIVE
    assert import_state(league_id).active_jobs == 1


# --------------------------------------------------------------------------
# 16, 17: T1 rollback
# --------------------------------------------------------------------------


def test_t1_failure_after_credential_creation_leaves_no_partial_connection(
    registry: Registry, league_id: str, espn: FakeEspn, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = create_caller(registry)
    espn.mode = "private"

    def explode(self: LeagueAccessRepository, **kwargs: Any) -> League:
        raise RuntimeError("injected failure after connection upsert")

    monkeypatch.setattr(LeagueAccessRepository, "lock_or_create_league", explode)

    with pytest.raises(RuntimeError, match="injected"):
        run_import(caller, league_id, espn, private_access=private())

    assert user_connections(caller.user_id) == []
    assert import_state(league_id).leagues == 0
    assert preference(caller.user_id) is None


def test_t1_failure_after_canonical_creation_preserves_other_committed_rows(
    registry: Registry, league_id: str, espn: FakeEspn, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = create_caller(registry), create_caller(registry)
    espn.mode = "private"
    committed = run_import(first, league_id, espn, private_access=private())
    before = import_state(league_id, user_ids=(first.user_id,))

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected failure after access creation")

    monkeypatch.setattr(league_import_module, "enqueue_season_sync", explode)

    # Same Season and a brand-new Season of the same League both roll back.
    for year in (SEASON, SEASON - 1):
        with pytest.raises(RuntimeError, match="injected"):
            run_import(second, league_id, espn, private_access=private(OTHER_S2), season_year=year)

    assert import_state(league_id, user_ids=(first.user_id,)) == before
    assert import_state(league_id, user_ids=(second.user_id,)).connections == 0
    access = access_row(first.user_id, committed.season_id)
    assert access is not None and access.status is UserSeasonAccessStatus.ACTIVE
    assert user_connections(first.user_id)[0].id == access.provider_connection_id
    assert access_row(second.user_id, committed.season_id) is None
    assert preference(second.user_id) is None


# --------------------------------------------------------------------------
# 18: default preference
# --------------------------------------------------------------------------


def test_default_preference_is_initialized_once(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    caller = create_caller(registry)

    first = run_import(caller, league_id, espn)
    second = run_import(caller, league_id, espn, season_year=SEASON - 1)

    assert first.season_id != second.season_id
    assert preference(caller.user_id) == first.season_id
    with SessionFactory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserPreference)
                .where(UserPreference.user_id == caller.user_id)
            )
            == 1
        )


# --------------------------------------------------------------------------
# 19: no transaction or connection during provider proof
# --------------------------------------------------------------------------


def test_provider_proof_runs_with_no_checked_out_database_connection(
    registry: Registry, league_id: str, api_espn: FakeEspn
) -> None:
    caller = create_caller(registry)
    observed: list[int] = []
    api_espn.mode = "private"
    api_espn.before_respond = lambda: observed.append(checked_out_connections())

    response = post_import(caller, import_body(league_id, credentials=credentials_body()))

    assert response.status_code == 200
    # Anonymous season and history endpoints, then the request's credentials.
    assert len(observed) == 3
    assert observed == [0, 0, 0]


# --------------------------------------------------------------------------
# 20: no secret or other-user leakage
# --------------------------------------------------------------------------


def test_no_secret_or_other_user_detail_leaks(
    registry: Registry,
    league_id: str,
    api_espn: FakeEspn,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    first, second = create_caller(registry), create_caller(registry)
    api_espn.mode = "private"

    success = post_import(first, import_body(league_id, credentials=credentials_body()))
    failure = post_import(second, import_body(league_id, credentials=credentials_body(WRONG_S2)))
    reuse = post_import(second, import_body(league_id, credentials=credentials_body(OTHER_S2)))

    assert success.status_code == 200 and reuse.status_code == 200
    assert failure.status_code == 422
    texts = [success.text, failure.text, reuse.text, caplog.text]
    for secret in (*SECRETS, "SECRET-PROVIDER-DETAIL"):
        assert all(secret not in value for value in texts)
    for value in (reuse.text, failure.text):
        assert str(first.user_id) not in value.replace(str(reuse.json()["season_id"]), "")
    assert reuse.json()["sync_state"] == "queued"  # same job reused, owner not disclosed

    connection = user_connections(first.user_id)[0]
    assert connection.encrypted_credentials is not None
    assert all(secret not in connection.encrypted_credentials for secret in SECRETS)
    vault = ProviderCredentialVault(keys=KEYRING.keys)
    envelope = vault.decrypt(
        encrypted_credentials=connection.encrypted_credentials,
        key_version=connection.credential_version or 0,
        expected_provider=FantasyProvider.ESPN,
    )
    assert envelope.credentials.values["espn_s2"] == SYNTHETIC_S2

    verified = VerifiedLeagueImport(
        provider=FantasyProvider.ESPN,
        provider_league_id=league_id,
        season_year=SEASON,
        league_name=LEAGUE_NAME,
        current_week=CURRENT_WEEK,
        regular_season_weeks=REGULAR_SEASON_WEEKS,
        proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
        private_access=private(),
    )
    assert all(secret not in repr(verified) for secret in SECRETS)


# --------------------------------------------------------------------------
# 21, 22, 23: unsupported providers, eligibility, request security
# --------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["sleeper", "yahoo"])
def test_sleeper_and_yahoo_persist_nothing(
    registry: Registry, league_id: str, api_espn: FakeEspn, provider: str
) -> None:
    caller = create_caller(registry)
    with SessionFactory() as session:
        limits_before = session.scalar(text("SELECT count(*) FROM registration_rate_limits"))

    response = post_import(caller, {**import_body(league_id), "provider": provider})

    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_provider"
    assert api_espn.calls == []
    assert import_state(league_id).leagues == 0
    with SessionFactory() as session:
        assert session.scalar(text("SELECT count(*) FROM registration_rate_limits")) == (
            limits_before
        )


@pytest.mark.parametrize(
    ("kwargs", "status_code"),
    [
        ({"role": UserRole.SPECTATOR}, 403),
        ({"verified": False}, 403),
        ({"active": False}, 401),
        ({"purpose": AuthSessionPurpose.DELETION_RECOVERY}, 403),
        ({"registration_status": WorkspaceRegistrationStatus.DELETION_PENDING}, 403),
        (
            {
                "registration_status": WorkspaceRegistrationStatus.PROVISIONAL,
                "installation_status": InstallationStatus.SETUP_IN_PROGRESS,
            },
            403,
        ),
    ],
)
def test_ineligible_callers_are_denied_without_proof_or_writes(
    registry: Registry,
    league_id: str,
    api_espn: FakeEspn,
    kwargs: dict[str, Any],
    status_code: int,
) -> None:
    caller = create_caller(registry, **kwargs)
    with SessionFactory() as session:
        limits_before = session.scalar(text("SELECT count(*) FROM registration_rate_limits"))

    response = post_import(caller, import_body(league_id))

    assert response.status_code == status_code
    assert api_espn.calls == []
    assert import_state(league_id).leagues == 0
    with SessionFactory() as session:
        assert session.scalar(text("SELECT count(*) FROM registration_rate_limits")) == (
            limits_before
        )


def test_eligibility_is_rechecked_in_t1_after_proof(
    registry: Registry, league_id: str, espn: FakeEspn
) -> None:
    caller = create_caller(registry)

    def revoke_during_proof() -> None:
        with SessionFactory() as session:
            session.execute(
                text("UPDATE auth_sessions SET revoked_at = now() WHERE id = :id"),
                {"id": caller.session_id},
            )
            session.commit()

    espn.before_respond = revoke_during_proof

    assert fail_import(caller, league_id, espn) == "league_import_forbidden"
    assert import_state(league_id).leagues == 0


@pytest.mark.parametrize(
    ("origin", "referer", "csrf"),
    [
        ("https://attacker.example", None, True),
        # Without Origin, an untrusted Referer is refused (a missing Origin is
        # tolerated outside production by the shared convention).
        (None, "https://attacker.example/page", True),
        ("http://localhost:5173", None, False),
    ],
)
def test_csrf_and_origin_are_enforced(
    registry: Registry,
    league_id: str,
    api_espn: FakeEspn,
    origin: str | None,
    referer: str | None,
    csrf: bool,
) -> None:
    caller = create_caller(registry)

    response = post_import(
        caller, import_body(league_id), origin=origin, referer=referer, csrf=csrf
    )

    assert response.status_code == 403
    assert api_espn.calls == []
    assert import_state(league_id).leagues == 0


def test_unauthenticated_request_is_rejected(league_id: str, api_espn: FakeEspn) -> None:
    response = post_import(None, import_body(league_id))

    assert response.status_code == 401
    assert api_espn.calls == []
