"""Committed fixtures and instrumentation for provider-synchronization tests.

Fixtures commit real rows in ``fantasy_what_if_test`` through separate
Sessions and clean them up afterwards. Provider adapters are synthetic and
in-process; nothing here performs network I/O.

The synchronization under test runs on a dedicated engine whose connections
carry a unique ``application_name``. An independent NullPool observer engine
inspects ``pg_stat_activity`` and ``pg_locks`` for exactly those backends,
so "no transaction, no row lock, no advisory lock during provider I/O" is
proven from the server's point of view, not inferred from source.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import requests
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.settings import Settings, get_settings
from app.db.models import (
    League,
    Matchup,
    OptimalLineupResult,
    PlayerWeekScore,
    Season,
    SyncRun,
    Team,
)
from app.db.platform_enums import (
    BackgroundJobType,
    FantasyProvider,
    FantasySport,
)
from app.db.platform_models import ProviderConnection
from app.db.session import SessionFactory
from app.db.session import engine as default_engine
from app.providers.credential_vault import ProviderCredentialVault
from app.providers.domain import (
    ProviderCapabilities,
    ProviderLeague,
    ProviderMatchup,
    ProviderPlayerWeek,
    ProviderScheduleRules,
    ProviderTeam,
    ProviderValidationResult,
    ProviderWeeklyRoster,
)
from app.providers.errors import ProviderErrorContext, ProviderUnavailableError
from app.providers.espn import credentials_from_values
from app.providers.registry import ProviderRegistry
from app.repositories.background_job_repository import BackgroundJobRepository
from app.services.background_job_worker import (
    BackgroundJobSnapshot,
    snapshot_background_job,
)
from app.services.sync_lock import PROVIDER_SYNC_LOCK_NAMESPACE
from tests.support.workspace_deletion_graph import (
    TEST_JOB_PRIORITY,
    cleanup_graphs,
    create_deletion_graph,
)

KEY = Fernet.generate_key().decode("ascii")
WORKER = "sync-boundary-worker"
LEASE = timedelta(minutes=5)
WAIT_SECONDS = 10
RAW_RESPONSE_MARKER = "RAW-ESPN-RESPONSE-BODY"


# --------------------------------------------------------------------------
# Synthetic provider
# --------------------------------------------------------------------------


class SyntheticSeasonAdapter:
    """Deterministic dataset keyed by ``dataset``; hooks run before each call."""

    provider = FantasyProvider.ESPN

    def __init__(
        self,
        *,
        provider_league_id: str,
        dataset: str,
        before_call: Callable[[str], None] | None = None,
        fail_on: str | None = None,
        season_year: int = 2026,
    ) -> None:
        self.provider_league_id = provider_league_id
        self.season_year = season_year
        self.dataset = dataset
        self.before_call = before_call
        self.fail_on = fail_on
        self.calls: list[str] = []

    def _enter(self, name: str) -> None:
        self.calls.append(name)

        if self.before_call is not None:
            self.before_call(name)

        if self.fail_on == name:
            raise ProviderUnavailableError(
                context=ProviderErrorContext(
                    provider=FantasyProvider.ESPN, operation=name, retryable=True
                ),
                safe_message=f"{RAW_RESPONSE_MARKER} upstream detail",
            )

    def validate_configuration(self) -> ProviderValidationResult:
        self._enter("validate_configuration")

        return ProviderValidationResult(
            is_valid=True, provider=self.provider, safe_message="Synthetic configuration."
        )

    def discover_leagues(self) -> tuple:
        raise AssertionError("Synchronization does not discover leagues.")

    def fetch_league(self, *, provider_league_id: str, provider_season_id: str) -> ProviderLeague:
        self._enter("fetch_league")
        assert provider_league_id == self.provider_league_id
        assert provider_season_id == str(self.season_year)

        return ProviderLeague(
            provider_league_id=self.provider_league_id,
            provider_season_id=str(self.season_year),
            name=f"Synthetic {self.dataset} League",
            sport=FantasySport.FOOTBALL,
            season_year=self.season_year,
            current_week=3,
            regular_season_weeks=3,
            playoff_week_start=None,
            playoff_week_end=None,
            scoring_type="head_to_head_points",
            logo_url=None,
            teams=tuple(self._team(index) for index in range(1, 5)),
            lineup_slots=(),
            scoring_rules=(),
            schedule_rules=ProviderScheduleRules(
                regular_season_weeks=3,
                playoff_week_start=None,
                playoff_week_end=None,
                has_divisions=False,
                has_median_game=False,
                has_doubleheaders=False,
                allows_ties=True,
            ),
            capabilities=ProviderCapabilities(
                supports_head_to_head=True,
                supports_weekly_matchups=True,
                supports_lineup_history=True,
                supports_optimal_lineups=False,
                supports_schedule_swap=True,
                supports_all_play=True,
                supports_league_average=True,
                supports_team_logos=False,
            ),
        )

    def fetch_matchups(
        self, *, provider_league_id: str, provider_season_id: str, weeks: tuple[int, ...]
    ) -> tuple[ProviderMatchup, ...]:
        self._enter("fetch_matchups")

        return tuple(
            ProviderMatchup(
                week=week,
                home_provider_team_id=self._team_id(home),
                away_provider_team_id=self._team_id(home + 1),
                home_score=Decimal("100.00"),
                away_score=Decimal("90.00"),
                is_complete=True,
                is_bye=False,
            )
            for week in weeks
            for home in (1, 3)
        )

    def fetch_weekly_rosters(
        self, *, provider_league_id: str, provider_season_id: str, weeks: tuple[int, ...]
    ) -> tuple[ProviderWeeklyRoster, ...]:
        self._enter("fetch_weekly_rosters")

        return tuple(
            ProviderWeeklyRoster(
                provider_team_id=self._team_id(team),
                week=week,
                players=(
                    ProviderPlayerWeek(
                        provider_player_id=f"{self.dataset}-{week}-{team}",
                        player_name=f"{self.dataset} Player {team}",
                        pro_team="HOU",
                        provider_position="WR",
                        canonical_position="WR",
                        actual_lineup_slot="WR",
                        eligible_lineup_slots=("WR", "BE"),
                        points=Decimal("12.50"),
                        projected_points=Decimal("11.00"),
                        is_starter=True,
                        is_bench=False,
                        is_injured_reserve=False,
                        is_on_roster=True,
                        metadata={"provider": "espn"},
                    ),
                ),
            )
            for week in weeks
            for team in range(1, 5)
        )

    def _team_id(self, index: int) -> str:
        return f"{self.dataset}-{index}"

    def _team(self, index: int) -> ProviderTeam:
        return ProviderTeam(
            provider_team_id=self._team_id(index),
            name=f"{self.dataset} Team {index}",
            wins=1,
            losses=1,
            ties=0,
            points_for=Decimal("200.00"),
            points_against=Decimal("190.00"),
        )


# --------------------------------------------------------------------------
# Instrumentation
# --------------------------------------------------------------------------


@dataclass
class Recorder:
    """Ordered, thread-safe log of session, SQL and provider events."""

    events: list[tuple[str, int]] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    closed: set[int] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, kind: str, ident: int = 0) -> None:
        with self.lock:
            self.events.append((kind, ident))

    def kinds(self) -> list[str]:
        with self.lock:
            return [kind for kind, _ in self.events]

    def index(self, kind: str, *, last: bool = False) -> int:
        kinds = self.kinds()
        positions = [position for position, value in enumerate(kinds) if value == kind]
        assert positions, f"{kind} was never recorded"

        return positions[-1] if last else positions[0]


@dataclass
class Harness:
    engine: Engine
    observer: Engine
    factory: sessionmaker[Session]
    recorder: Recorder
    application_name: str

    def backend_activity(self) -> list[tuple[str, str | None]]:
        """(state, wait_event_type) for every backend of the sync engine."""

        with self.observer.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT state, wait_event_type FROM pg_stat_activity "
                    "WHERE application_name = :name AND pid <> pg_backend_pid()"
                ),
                {"name": self.application_name},
            ).all()

        return [(row[0], row[1]) for row in rows]

    def backend_lock_count(self) -> int:
        """Every lock (row, relation, transaction id, advisory) the sync holds."""

        with self.observer.connect() as connection:
            return int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a "
                        "ON a.pid = l.pid WHERE a.application_name = :name"
                    ),
                    {"name": self.application_name},
                )
                or 0
            )

    def season_advisory_lock_count(self, season_id: int) -> int:
        with self.observer.connect() as connection:
            return int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND classid::bigint = :namespace AND objid::bigint = :season_id "
                        "AND objsubid = 2"
                    ),
                    {"namespace": PROVIDER_SYNC_LOCK_NAMESPACE, "season_id": season_id},
                )
                or 0
            )

    def waiting_on_lock(self) -> bool:
        return any(wait == "Lock" for _, wait in self.backend_activity())

    def wait_until_blocked(self) -> None:
        """Block until a sync backend waits on a PostgreSQL lock (no sleeps)."""

        deadline = datetime.now(UTC) + timedelta(seconds=WAIT_SECONDS)

        while not self.waiting_on_lock():
            assert datetime.now(UTC) < deadline, "The synchronization never blocked on a lock."

            with self.observer.connect() as connection:
                connection.execute(text("SELECT pg_sleep(0.01)"))

    def assert_no_database_state(self, season_id: int) -> None:
        """The provider is being called: nothing may be open or held."""

        with self.recorder.lock:
            sessions = list(self.recorder.sessions)
            closed = set(self.recorder.closed)

        for session in sessions:
            assert id(session) in closed, "A synchronization Session is still open."
            assert not session.in_transaction()

        assert self.engine.pool.checkedout() == 0
        assert all(state == "idle" for state, _ in self.backend_activity())
        assert self.backend_lock_count() == 0
        assert self.season_advisory_lock_count(season_id) == 0


@contextmanager
def harness_context() -> Iterator[Harness]:
    application_name = f"sync-boundary-{uuid4().hex[:12]}"
    sync_engine = create_engine(
        default_engine.url,
        connect_args={"application_name": application_name},
        pool_size=5,
    )
    observer = create_engine(default_engine.url, poolclass=NullPool)
    recorder = Recorder()

    class RecordingSession(Session):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)

            with recorder.lock:
                recorder.sessions.append(self)

            recorder.log("session_open", id(self))

        def close(self) -> None:
            with recorder.lock:
                recorder.closed.add(id(self))

            recorder.log("session_close", id(self))
            super().close()

    @event.listens_for(sync_engine, "before_cursor_execute")
    def record_sql(conn, cursor, statement, parameters, context, executemany) -> None:
        if "pg_try_advisory_xact_lock" in statement:
            recorder.log("advisory_lock")
        if "FROM installations" in statement and "FOR UPDATE" in statement:
            recorder.log("workspace_lock")

    @event.listens_for(RecordingSession, "after_begin")
    def record_begin(session, transaction, connection) -> None:
        recorder.log("begin", id(session))

    @event.listens_for(RecordingSession, "after_commit")
    def record_commit(session) -> None:
        recorder.log("commit", id(session))

    factory = sessionmaker(
        bind=sync_engine,
        class_=RecordingSession,
        autoflush=False,
        expire_on_commit=False,
    )

    try:
        yield Harness(
            engine=sync_engine,
            observer=observer,
            factory=factory,
            recorder=recorder,
            application_name=application_name,
        )
    finally:
        sync_engine.dispose()
        observer.dispose()


def forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Live network requests are forbidden in tests.")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


# --------------------------------------------------------------------------
# Committed fixtures
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncGraph:
    workspace_id: int
    league_id: int
    season_id: int
    connection_id: int
    provider_league_id: str
    swid: str
    espn_s2: str


def encrypt_synthetic_credentials(provider_league_id: str, *, swid: str, espn_s2: str) -> str:
    return ProviderCredentialVault(keys={1: KEY}).encrypt(
        provider=FantasyProvider.ESPN,
        credentials=credentials_from_values(
            league_id=provider_league_id, season="2026", espn_s2=espn_s2, swid=swid
        ),
        key_version=1,
    )


@contextmanager
def graph_factory() -> Iterator[Callable[[], SyncGraph]]:
    created: list[int] = []

    def create() -> SyncGraph:
        graph = create_deletion_graph(seasons=1, pending=False)
        created.append(graph.workspace_id)
        suffix = uuid4().hex[:10]
        swid = f"{{SYNTH-SWID-{suffix}}}"
        espn_s2 = f"SYNTH-ESPN-S2-{suffix}"

        with SessionFactory() as session:
            connection = session.get(ProviderConnection, graph.connection_id)
            assert connection is not None
            connection.encrypted_credentials = encrypt_synthetic_credentials(
                graph.provider_league_id, swid=swid, espn_s2=espn_s2
            )
            connection.credential_version = 1
            session.commit()

        return SyncGraph(
            workspace_id=graph.workspace_id,
            league_id=graph.league_id,
            season_id=graph.season_ids[0],
            connection_id=graph.connection_id,
            provider_league_id=graph.provider_league_id,
            swid=swid,
            espn_s2=espn_s2,
        )

    yield create
    cleanup_graphs(created)


def worker_settings() -> Settings:
    return get_settings().model_copy(
        update={
            "provider_credential_keys": SecretStr(f'{{"1": "{KEY}"}}'),
            "provider_credential_current_key_version": 1,
        }
    )


def registry_for(adapter: SyntheticSeasonAdapter) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(FantasyProvider.ESPN, lambda credentials: adapter)

    return registry


def enqueue_sync_job(graph: SyncGraph) -> int:
    with SessionFactory() as session:
        result = BackgroundJobRepository(session).enqueue(
            workspace_id=graph.workspace_id,
            job_type=BackgroundJobType.FULL_SEASON_SYNC,
            payload={"season_id": graph.season_id, "full_sync": True, "trigger": "boundary"},
            idempotency_key=f"sync-boundary:{graph.workspace_id}:{uuid4().hex}",
            priority=TEST_JOB_PRIORITY,
            maximum_attempts=3,
        )
        session.commit()

        return result.job.id


def claim(job_id: int) -> BackgroundJobSnapshot:
    with SessionFactory() as session:
        job = BackgroundJobRepository(session).claim_next_available(
            worker_id=WORKER, lease_duration=LEASE
        )
        assert job is not None and job.id == job_id
        snapshot = snapshot_background_job(job)
        session.commit()

    return snapshot


def canonical_state(graph: SyncGraph) -> dict[str, Any]:
    """Every canonical and derived value a synchronization may write."""

    season_id = graph.season_id

    with SessionFactory() as session:

        def count(model: Any) -> int:
            return int(
                session.scalar(
                    select(func.count()).select_from(model).where(model.season_id == season_id)
                )
                or 0
            )

        season = session.get(Season, season_id)
        league = session.get(League, graph.league_id)
        assert season is not None and league is not None

        return {
            "teams": sorted(
                session.scalars(select(Team.provider_team_id).where(Team.season_id == season_id))
            ),
            "matchups": count(Matchup),
            "player_week_scores": sorted(
                session.scalars(
                    select(PlayerWeekScore.provider_player_id).where(
                        PlayerWeekScore.season_id == season_id
                    )
                )
            ),
            "optimal_lineup_results": count(OptimalLineupResult),
            "league_name": league.name,
            "season_last_synced_at": season.last_synced_at,
            "season_team_count": season.team_count,
            "season_scoring_type": season.scoring_type,
        }


def sync_runs(season_id: int) -> list[SyncRun]:
    with SessionFactory() as session:
        runs = list(
            session.scalars(
                select(SyncRun).where(SyncRun.season_id == season_id).order_by(SyncRun.id)
            )
        )
        for run in runs:
            session.expunge(run)

        return runs


def latest_run(season_id: int) -> SyncRun:
    return sync_runs(season_id)[-1]


def run_in_thread(target: Callable[[], object]) -> tuple[threading.Thread, list[object]]:
    outcome: list[object] = []

    def wrapped() -> None:
        try:
            outcome.append(target())
        except BaseException as exc:  # noqa: BLE001 - asserted by the caller
            outcome.append(exc)

    thread = threading.Thread(target=wrapped, daemon=True)
    thread.start()

    return thread, outcome


# --------------------------------------------------------------------------
# 1, 2, 11: provider I/O holds nothing; worker path; cross-workspace isolation
