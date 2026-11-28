"""In-flight shared Season synchronization versus personal authority removal (ADR 0010).

Real PostgreSQL (``fantasy_what_if_test``), the real prepare, fetch and persist path of
``synchronize_season`` on its own pooled engine, a claimed Season-sync job, and the real
account-deletion and League-removal services. The provider call is paused with an event
while no database transaction is open; the authority removal commits on a separate
connection; the call is then released. No sleeps.
"""

# ruff: noqa: F811  (pytest fixtures are imported and then requested by parameter name)

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.db.auth_models import AuthSession, User
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League, Matchup, PlayerWeekScore, Season, SyncRun, SyncStatus, Team
from app.db.platform_enums import (
    BackgroundJobStatus,
    FantasyProvider,
    FantasySport,
    ProviderAccessMode,
    SeasonStatus,
    UserSeasonAccessStatus,
    UserSeasonProofMode,
)
from app.db.platform_models import BackgroundJob, ProviderConnection
from app.db.session import SessionFactory
from app.services.background_job_worker import BackgroundJobExecutionError
from app.services.provider_sync import SyncJobAuthority, synchronize_season
from tests.support.league_import import (  # noqa: F401
    Caller,
    create_caller,
    forbid_live_network,
    registry,
    unique_league_id,
)
from tests.support.provider_sync_fixtures import (
    Harness,
    SyntheticSeasonAdapter,
    claim,
    encrypt_synthetic_credentials,
    enqueue_sync_job,
    harness_context,
    worker_settings,
)
from tests.test_account_deletion_critical import Pause, Worker, delete_account, residue  # noqa: F401
from tests.test_league_removal_critical import remove
from tests.test_league_season_sync_critical import RecordingProvider

YEAR = 2026
PRIVATE = UserSeasonProofMode.PRIVATE_CREDENTIALS
PUBLIC = UserSeasonProofMode.PUBLIC


@pytest.fixture
def harness() -> Iterator[Harness]:
    with harness_context() as created:
        yield created


@dataclass
class Member:
    caller: Caller
    swid: str
    espn_s2: str
    connection_id: int | None
    ciphertext: str | None
    email: str
    display_name: str


@dataclass
class Target:
    league_pk: int
    season_id: int
    provider_league_id: str
    name: str


@pytest.fixture
def target(registry) -> Iterator[Target]:
    provider_league_id = unique_league_id()
    registry.league_ids.append(provider_league_id)
    name = f"Race League {uuid4().hex[:8]}"

    with SessionFactory() as session:
        league = League(
            name=name,
            provider=FantasyProvider.ESPN,
            provider_league_id=provider_league_id,
            sport=FantasySport.FOOTBALL,
        )
        session.add(league)
        session.flush()
        season = Season(
            league_id=league.id,
            year=YEAR,
            current_week=1,
            regular_season_weeks=3,
            provider_season_id=str(YEAR),
            status=SeasonStatus.ACTIVE,
        )
        session.add(season)
        session.commit()
        created = Target(league.id, season.id, provider_league_id, name)

    yield created

    with SessionFactory() as session:
        session.execute(
            delete(BackgroundJob).where(
                BackgroundJob.idempotency_key == f"league-season-sync:{created.season_id}"
            )
        )
        session.commit()


def member(registry, target: Target, *, proof: UserSeasonProofMode = PRIVATE) -> Member:
    caller = create_caller(registry, personal=True)
    suffix = uuid4().hex[:10]
    swid, espn_s2 = f"{{RACE-SWID-{suffix}}}", f"RACE-S2-{suffix}"
    connection_id: int | None = None
    ciphertext: str | None = None

    with SessionFactory() as session:
        user = session.get(User, caller.user_id)
        email, display_name = user.normalized_email, user.display_name

        if proof is PRIVATE:
            ciphertext = encrypt_synthetic_credentials(
                target.provider_league_id, swid=swid, espn_s2=espn_s2
            )
            connection = ProviderConnection(
                user_id=caller.user_id,
                provider=FantasyProvider.ESPN,
                display_name=f"Race {suffix}",
                encrypted_credentials=ciphertext,
                credential_version=1,
                is_active=True,
                provider_league_id=target.provider_league_id,
                provider_season_year=YEAR,
                access_mode=ProviderAccessMode.PRIVATE,
            )
            session.add(connection)
            session.flush()
            connection_id = connection.id

        session.add(
            UserLeagueSeasonAccess(
                user_id=caller.user_id,
                season_id=target.season_id,
                provider_connection_id=connection_id,
                proof_mode=proof,
                status=UserSeasonAccessStatus.ACTIVE,
                verified_at=datetime.now(UTC),
            )
        )
        session.commit()

    return Member(caller, swid, espn_s2, connection_id, ciphertext, email, display_name)


def canonical(target: Target) -> dict[str, Any]:
    with SessionFactory() as session:
        season = session.get(Season, target.season_id)
        league = session.get(League, target.league_pk)

        def count(model: Any) -> int:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.season_id == target.season_id)
                )
                or 0
            )

        return {
            "teams": sorted(
                session.scalars(select(Team.provider_team_id).where(Team.season_id == season.id))
            ),
            "matchups": count(Matchup),
            "player_scores": count(PlayerWeekScore),
            "data_version": season.data_version,
            "last_successful_sync_at": season.last_successful_sync_at,
            "league_name": league.name,
        }


def runs(target: Target) -> list[SyncRun]:
    with SessionFactory() as session:
        rows = list(
            session.scalars(
                select(SyncRun).where(SyncRun.season_id == target.season_id).order_by(SyncRun.id)
            )
        )
        session.expunge_all()
        return rows


def access_of(user_id: int) -> list[int]:
    with SessionFactory() as session:
        return list(
            session.scalars(
                select(UserLeagueSeasonAccess.id).where(UserLeagueSeasonAccess.user_id == user_id)
            )
        )


def connections_of(user_id: int) -> list[int]:
    with SessionFactory() as session:
        return list(
            session.scalars(
                select(ProviderConnection.id).where(ProviderConnection.user_id == user_id)
            )
        )


@dataclass
class Race:
    error: BaseException | None
    adapter: SyntheticSeasonAdapter
    recording: RecordingProvider
    job_id: int
    texts: list[str]


def run_race(
    harness: Harness,
    target: Target,
    caplog: pytest.LogCaptureFixture,
    *,
    remove_authority: Callable[[], None],
) -> Race:
    """Pause the provider call, commit the removal, release, and collect the outcome."""

    caplog.set_level(logging.DEBUG)
    pause = Pause("sync")
    provider = SyntheticSeasonAdapter(
        provider_league_id=target.provider_league_id,
        dataset="race",
        before_call=lambda _name: pause(),
        season_year=YEAR,
    )
    recording = RecordingProvider(provider)
    job_id = enqueue_sync_job(target)  # type: ignore[arg-type]
    snapshot = claim(job_id)

    def sync() -> Any:
        return synchronize_season(
            session_factory=harness.factory,
            season_id=target.season_id,
            settings=worker_settings(),
            registry=recording.registry(),
            job_authority=SyncJobAuthority(
                job_id=snapshot.id, attempt_count=snapshot.attempt_count
            ),
        )

    worker = Worker("sync", sync)
    worker.start()
    pause.wait_reached()

    # The plan is detached and the provider is being called: nothing is open or held.
    harness.assert_no_database_state(target.season_id)
    remove_authority()
    pause.release.set()
    worker.join(15)
    assert not worker.is_alive()

    with SessionFactory() as session:
        job = session.get(BackgroundJob, job_id)
        texts = [
            caplog.text,
            str(worker.error),
            getattr(worker.error, "safe_error_message", "") or "",
            job.safe_error_code or "",
            job.safe_error_message or "",
            str(job.payload),
            job.idempotency_key or "",
            *(run.safe_error_message or "" for run in runs(target)),
        ]
        assert job.status is BackgroundJobStatus.RUNNING  # outcome persistence is the worker's job

    return Race(worker.error, provider, recording, job_id, texts)


def assert_no_leaks(race: Race, *removed: Member) -> None:
    for text in race.texts:
        for gone in removed:
            for secret in (gone.swid, gone.espn_s2, gone.ciphertext, gone.email, gone.display_name):
                assert secret is None or secret not in text
            assert re.search(rf"connection[_ ]?id\W+{gone.connection_id}\b", text) is None
            assert re.search(rf"user[_ ]?id\W+{gone.caller.user_id}\b", text) is None


def assert_plan_rejected(race: Race, target: Target, before: dict[str, Any]) -> None:
    assert isinstance(race.error, BackgroundJobExecutionError)
    assert race.error.safe_error_code in {
        "provider_connection_changed",
        "provider_access_mode_changed",
    }
    assert race.adapter.calls  # the provider really was called, with the detached plan
    after = canonical(target)
    assert after == before  # no canonical or derived write; data_version unchanged
    attempts = runs(target)
    assert len(attempts) == 1 and attempts[0].status is SyncStatus.FAILED
    assert not any(run.status is SyncStatus.SUCCEEDED for run in attempts)


def removers(registry_member: Member, target: Target) -> dict[str, Callable[[], None]]:
    return {
        "account_deletion": lambda: delete_account(registry_member.caller),
        "league_removal": lambda: remove(
            registry_member.caller, target.provider_league_id, confirmation=target.name
        ),
    }


@pytest.mark.parametrize("path", ["account_deletion", "league_removal"])
def test_removal_during_fetch_rejects_the_detached_plan_when_no_one_else_remains(
    registry, target, harness, caplog, path
):
    leaving = member(registry, target)
    before = canonical(target)
    connection_ciphertext = leaving.ciphertext

    race = run_race(harness, target, caplog, remove_authority=removers(leaving, target)[path])

    assert_plan_rejected(race, target, before)
    assert race.recording.credentials[0]["swid"] == leaving.swid  # it was this user's plan
    assert access_of(leaving.caller.user_id) == []
    assert connections_of(leaving.caller.user_id) == []  # never restored by the stale plan
    assert connection_ciphertext not in {  # nor recreated anywhere
        row for row in _all_ciphertexts(target)
    }
    with SessionFactory() as session:
        league = session.get(League, target.league_pk)
        assert league is not None and league.orphaned_at is not None  # retention, not deletion
        assert session.get(Season, target.season_id) is not None
        assert (session.get(User, leaving.caller.user_id) is None) is (path == "account_deletion")
    assert_no_leaks(race, leaving)

    # No authority is left, so a retry does not invent one.
    with pytest.raises(BackgroundJobExecutionError) as retry:
        synchronize_season(
            session_factory=SessionFactory,
            season_id=target.season_id,
            settings=worker_settings(),
            registry=race.recording.registry(),
        )
    assert retry.value.safe_error_code == "provider_reverification_required"
    assert canonical(target) == before

    # Anonymous public authority from someone else supports a later retry.
    public = member(registry, target, proof=PUBLIC)
    synchronize_season(
        session_factory=SessionFactory,
        season_id=target.season_id,
        settings=worker_settings(),
        registry=race.recording.registry(),
    )
    assert canonical(target)["data_version"] == before["data_version"] + 1
    assert leaving.swid not in str(race.recording.credentials[-1])
    assert access_of(public.caller.user_id) != []


@pytest.mark.parametrize("path", ["account_deletion", "league_removal"])
def test_other_eligible_user_keeps_authority_but_the_inflight_plan_still_loses(
    registry, target, harness, caplog, path
):
    leaving = member(registry, target)
    remaining = member(registry, target)
    before = canonical(target)
    remaining_before = (
        access_of(remaining.caller.user_id),
        connections_of(remaining.caller.user_id),
    )
    with SessionFactory() as session:
        remaining_ciphertext = session.get(
            ProviderConnection, remaining.connection_id
        ).encrypted_credentials

    race = run_race(harness, target, caplog, remove_authority=removers(leaving, target)[path])

    assert_plan_rejected(race, target, before)
    # The attempt used the leaving user's credentials and never switched after the fetch.
    assert [item["swid"] for item in race.recording.credentials] == [leaving.swid]
    assert race.adapter.calls.count("fetch_league") == 1
    assert access_of(leaving.caller.user_id) == [] and connections_of(leaving.caller.user_id) == []
    # The other user's authority is untouched.
    assert (
        access_of(remaining.caller.user_id),
        connections_of(remaining.caller.user_id),
    ) == remaining_before
    with SessionFactory() as session:
        assert (
            session.get(ProviderConnection, remaining.connection_id).encrypted_credentials
            == remaining_ciphertext
        )
        assert session.get(League, target.league_pk).orphaned_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.user_id == remaining.caller.user_id)
            )
            == 1
        )
    assert_no_leaks(race, leaving)

    # A later retry selects the remaining connection and succeeds.
    later = RecordingProvider(
        SyntheticSeasonAdapter(
            provider_league_id=target.provider_league_id, dataset="retry", season_year=YEAR
        )
    )
    synchronize_season(
        session_factory=SessionFactory,
        season_id=target.season_id,
        settings=worker_settings(),
        registry=later.registry(),
    )
    assert [item["swid"] for item in later.credentials] == [remaining.swid]
    assert canonical(target)["data_version"] == before["data_version"] + 1
    assert [run.status for run in runs(target)] == [SyncStatus.FAILED, SyncStatus.SUCCEEDED]
    assert leaving.swid not in str(later.credentials) and leaving.espn_s2 not in str(
        later.credentials
    )


def _all_ciphertexts(target: Target) -> set[str]:
    with SessionFactory() as session:
        return set(
            session.scalars(
                select(ProviderConnection.encrypted_credentials).where(
                    ProviderConnection.provider_league_id == target.provider_league_id
                )
            )
        )
