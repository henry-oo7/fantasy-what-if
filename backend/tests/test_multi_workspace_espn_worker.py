from collections.abc import Iterator
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Matchup,
    SyncRun,
    SyncStatus,
    Team,
)
from app.db.platform_enums import (
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.db.platform_models import BackgroundJob
from app.db.session import engine
from app.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from app.services.background_job_handlers import (
    build_background_job_handlers,
)
from app.services.background_job_worker import (
    execute_one_background_job,
)
from tests.support.credential_aware_espn import (
    credential_aware_espn_registry,
)
from tests.support.multi_workspace_espn import (
    FIRST_COOKIE_MARKER,
    FIRST_SWID_MARKER,
    SECOND_COOKIE_MARKER,
    SECOND_SWID_MARKER,
    MultiWorkspaceEspnFixture,
    create_multi_workspace_espn_fixture,
)


@pytest.fixture
def isolated_session_factory() -> Iterator[sessionmaker[Session]]:
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield factory
    finally:
        if transaction.is_active:
            transaction.rollback()

        connection.close()


def create_fixture(
    factory: sessionmaker[Session],
) -> MultiWorkspaceEspnFixture:
    with factory() as session:
        fixture = create_multi_workspace_espn_fixture(session)
        session.commit()

    return fixture


def enqueue_workspace_job(
    factory: sessionmaker[Session],
    *,
    workspace_id: int,
    season_id: int,
) -> int:
    with factory() as session:
        result = BackgroundJobRepository(session).enqueue(
            workspace_id=workspace_id,
            job_type=(BackgroundJobType.INCREMENTAL_SEASON_SYNC),
            payload={
                "season_id": season_id,
                "full_sync": False,
                "trigger": "multi_workspace_test",
            },
            idempotency_key=(f"multi-workspace:{workspace_id}:{uuid4().hex}"),
            priority=100,
            maximum_attempts=5,
        )
        job_id = result.job.id

        assert job_id is not None

        session.commit()

    return job_id


def execute_next_job(
    factory: sessionmaker[Session],
    *,
    handlers,
):
    return execute_one_background_job(
        session_factory=factory,
        worker_id="multi-workspace-worker",
        lease_duration=timedelta(minutes=5),
        heartbeat_interval=timedelta(minutes=1),
        handlers=handlers,
        jitter_ratio=0,
    )


def test_two_workspace_jobs_execute_with_isolated_credentials_and_data(
    isolated_session_factory: sessionmaker[Session],
) -> None:
    fixture = create_fixture(isolated_session_factory)
    registry, adapter_factory = credential_aware_espn_registry()
    handlers = build_background_job_handlers(
        session_factory=isolated_session_factory,
        settings=fixture.settings(),
        registry=registry,
    )

    first_job_id = enqueue_workspace_job(
        isolated_session_factory,
        workspace_id=fixture.first.workspace_id,
        season_id=fixture.first.season_id,
    )
    second_job_id = enqueue_workspace_job(
        isolated_session_factory,
        workspace_id=fixture.second.workspace_id,
        season_id=fixture.second.season_id,
    )

    first_result = execute_next_job(
        isolated_session_factory,
        handlers=handlers,
    )
    second_result = execute_next_job(
        isolated_session_factory,
        handlers=handlers,
    )

    assert first_result.job_id == first_job_id
    assert first_result.succeeded is True
    assert second_result.job_id == second_job_id
    assert second_result.succeeded is True

    assert adapter_factory.received_credentials == [
        {
            "league_id": (fixture.first.provider_league_id),
            "season": "2026",
            "espn_s2": FIRST_COOKIE_MARKER,
            "swid": FIRST_SWID_MARKER,
        },
        {
            "league_id": (fixture.second.provider_league_id),
            "season": "2026",
            "espn_s2": SECOND_COOKIE_MARKER,
            "swid": SECOND_SWID_MARKER,
        },
    ]

    assert [adapter.dataset_marker for adapter in adapter_factory.adapters] == [
        "alpha",
        "beta",
    ]

    with isolated_session_factory() as session:
        first_job = session.get(
            BackgroundJob,
            first_job_id,
        )
        second_job = session.get(
            BackgroundJob,
            second_job_id,
        )

        assert first_job is not None
        assert second_job is not None
        assert first_job.status is (BackgroundJobStatus.SUCCEEDED)
        assert second_job.status is (BackgroundJobStatus.SUCCEEDED)

        for job in (
            first_job,
            second_job,
        ):
            assert "espn_s2" not in job.payload
            assert "swid" not in job.payload
            assert "provider_credentials" not in job.payload
            assert "encrypted_credentials" not in job.payload

        first_teams = tuple(
            session.scalars(
                select(Team)
                .where(
                    Team.season_id == fixture.first.season_id,
                    Team.provider_team_id.like("alpha-%"),
                )
                .order_by(Team.provider_team_id)
            )
        )
        second_teams = tuple(
            session.scalars(
                select(Team)
                .where(
                    Team.season_id == fixture.second.season_id,
                    Team.provider_team_id.like("beta-%"),
                )
                .order_by(Team.provider_team_id)
            )
        )

        assert [team.provider_team_id for team in first_teams] == [
            "alpha-team-1",
            "alpha-team-2",
        ]
        assert [team.name for team in first_teams] == [
            "ALPHA Team 1",
            "ALPHA Team 2",
        ]
        assert [team.provider_team_id for team in second_teams] == [
            "beta-team-1",
            "beta-team-2",
        ]
        assert [team.name for team in second_teams] == [
            "BETA Team 1",
            "BETA Team 2",
        ]

        assert not tuple(
            session.scalars(
                select(Team).where(
                    Team.season_id == fixture.first.season_id,
                    Team.provider_team_id.like("beta-%"),
                )
            )
        )
        assert not tuple(
            session.scalars(
                select(Team).where(
                    Team.season_id == fixture.second.season_id,
                    Team.provider_team_id.like("alpha-%"),
                )
            )
        )

        first_matchups = tuple(
            session.scalars(
                select(Matchup).where(
                    Matchup.season_id == fixture.first.season_id,
                    Matchup.week.in_((1, 2)),
                )
            )
        )
        second_matchups = tuple(
            session.scalars(
                select(Matchup).where(
                    Matchup.season_id == fixture.second.season_id,
                    Matchup.week.in_((1, 2)),
                )
            )
        )

        assert len(first_matchups) == 2
        assert len(second_matchups) == 2
        assert all(matchup.season_id == fixture.first.season_id for matchup in first_matchups)
        assert all(matchup.season_id == fixture.second.season_id for matchup in second_matchups)

        first_run = session.scalar(
            select(SyncRun)
            .where(SyncRun.season_id == fixture.first.season_id)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
        second_run = session.scalar(
            select(SyncRun)
            .where(SyncRun.season_id == fixture.second.season_id)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )

        assert first_run is not None
        assert second_run is not None
        assert first_run.status is (SyncStatus.SUCCEEDED)
        assert second_run.status is (SyncStatus.SUCCEEDED)
        assert first_run.provider_connection_id == fixture.first.provider_connection_id
        assert second_run.provider_connection_id == fixture.second.provider_connection_id

        assert (
            BackgroundJobRepository(session).get_for_workspace(
                job_id=second_job_id,
                workspace_id=(fixture.first.workspace_id),
            )
            is None
        )


def test_provider_authentication_failure_is_isolated(
    isolated_session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.db.platform_enums import FantasyProvider
    from app.providers.domain import (
        ProviderCredentials,
    )
    from app.providers.errors import (
        ProviderAuthenticationError,
        ProviderErrorContext,
    )
    from app.providers.registry import ProviderRegistry
    from tests.support.credential_aware_espn import (
        CredentialAwareEspnAdapter,
    )

    fixture = create_fixture(isolated_session_factory)
    received_credentials: list[dict[str, str]] = []

    class RejectingEspnAdapter:
        provider = FantasyProvider.ESPN

        def validate_configuration(self):
            raise AssertionError("Worker synchronization does not use configuration validation.")

        def discover_leagues(self):
            raise AssertionError("Worker synchronization does not use league discovery.")

        def fetch_league(
            self,
            *,
            provider_league_id: str,
            provider_season_id: str,
        ):
            del provider_league_id
            del provider_season_id

            raise ProviderAuthenticationError(
                context=ProviderErrorContext(
                    provider=FantasyProvider.ESPN,
                    operation="fetch_league",
                    retryable=False,
                ),
            )

        def fetch_matchups(
            self,
            *,
            provider_league_id: str,
            provider_season_id: str,
            weeks: tuple[int, ...],
        ):
            del provider_league_id
            del provider_season_id
            del weeks

            raise AssertionError("Authentication failure should occur before matchup retrieval.")

        def fetch_weekly_rosters(
            self,
            *,
            provider_league_id: str,
            provider_season_id: str,
            weeks: tuple[int, ...],
        ):
            del provider_league_id
            del provider_season_id
            del weeks

            raise AssertionError("Authentication failure should occur before roster retrieval.")

    def adapter_factory(
        credentials: ProviderCredentials,
    ):
        values = dict(credentials.values)
        received_credentials.append(values)

        league_id = values.get("league_id")
        season = values.get("season")
        cookie = values.get("espn_s2")
        swid = values.get("swid")

        if (
            league_id == fixture.first.provider_league_id
            and cookie == FIRST_COOKIE_MARKER
            and swid == FIRST_SWID_MARKER
            and season == "2026"
        ):
            return CredentialAwareEspnAdapter(
                provider_league_id=league_id,
                provider_season_id=season,
                cookie_marker=cookie,
                swid_marker=swid,
                dataset_marker="alpha",
            )

        if (
            league_id == fixture.second.provider_league_id
            and cookie == SECOND_COOKIE_MARKER
            and swid == SECOND_SWID_MARKER
            and season == "2026"
        ):
            return RejectingEspnAdapter()

        raise AssertionError("The failure-isolation factory received unexpected credentials.")

    registry = ProviderRegistry()
    registry.register(
        FantasyProvider.ESPN,
        adapter_factory,
    )
    handlers = build_background_job_handlers(
        session_factory=isolated_session_factory,
        settings=fixture.settings(),
        registry=registry,
    )

    first_job_id = enqueue_workspace_job(
        isolated_session_factory,
        workspace_id=fixture.first.workspace_id,
        season_id=fixture.first.season_id,
    )
    second_job_id = enqueue_workspace_job(
        isolated_session_factory,
        workspace_id=fixture.second.workspace_id,
        season_id=fixture.second.season_id,
    )

    with caplog.at_level("INFO"):
        first_result = execute_next_job(
            isolated_session_factory,
            handlers=handlers,
        )
        second_result = execute_next_job(
            isolated_session_factory,
            handlers=handlers,
        )

    assert first_result.job_id == first_job_id
    assert first_result.succeeded is True
    assert first_result.failed is False

    assert second_result.job_id == second_job_id
    assert second_result.succeeded is False
    assert second_result.retry_scheduled is False
    assert second_result.failed is True

    assert received_credentials == [
        {
            "league_id": (fixture.first.provider_league_id),
            "season": "2026",
            "espn_s2": FIRST_COOKIE_MARKER,
            "swid": FIRST_SWID_MARKER,
        },
        {
            "league_id": (fixture.second.provider_league_id),
            "season": "2026",
            "espn_s2": SECOND_COOKIE_MARKER,
            "swid": SECOND_SWID_MARKER,
        },
    ]

    with isolated_session_factory() as session:
        first_job = session.get(
            BackgroundJob,
            first_job_id,
        )
        second_job = session.get(
            BackgroundJob,
            second_job_id,
        )

        assert first_job is not None
        assert second_job is not None

        assert first_job.status is (BackgroundJobStatus.SUCCEEDED)
        assert first_job.safe_error_code is None
        assert first_job.safe_error_message is None

        assert second_job.status is (BackgroundJobStatus.FAILED)
        assert second_job.safe_error_code == ("authentication")
        assert second_job.safe_error_message == ("The fantasy provider credentials were rejected.")
        assert second_job.retryable is False

        first_teams = tuple(
            session.scalars(
                select(Team).where(
                    Team.season_id == fixture.first.season_id,
                    Team.provider_team_id.like("alpha-%"),
                )
            )
        )
        second_teams = tuple(
            session.scalars(
                select(Team).where(
                    Team.season_id == fixture.second.season_id,
                    Team.provider_team_id.like("beta-%"),
                )
            )
        )

        assert len(first_teams) == 2
        assert second_teams == ()

        first_run = session.scalar(
            select(SyncRun)
            .where(SyncRun.season_id == fixture.first.season_id)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
        second_run = session.scalar(
            select(SyncRun)
            .where(SyncRun.season_id == fixture.second.season_id)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )

        assert first_run is not None
        assert first_run.status is (SyncStatus.SUCCEEDED)
        assert second_run is None

        assert (
            BackgroundJobRepository(session).get_for_workspace(
                job_id=second_job_id,
                workspace_id=(fixture.first.workspace_id),
            )
            is None
        )

        serialized_job_data = " ".join(
            (
                str(first_job.payload),
                str(second_job.payload),
                str(first_job.safe_error_code),
                str(first_job.safe_error_message),
                str(second_job.safe_error_code),
                str(second_job.safe_error_message),
            )
        )
        captured_logs = caplog.text

        for secret in (
            FIRST_COOKIE_MARKER,
            FIRST_SWID_MARKER,
            SECOND_COOKIE_MARKER,
            SECOND_SWID_MARKER,
        ):
            assert secret not in serialized_job_data
            assert secret not in captured_logs
