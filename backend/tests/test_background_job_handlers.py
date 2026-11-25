from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.db.platform_enums import BackgroundJobType
from app.services.background_job_handlers import (
    SynchronizationBackgroundJobHandler,
    build_background_job_handlers,
    reject_secret_payload_fields,
    validate_sync_job_ownership,
    validate_sync_job_payload,
)
from app.services.background_job_worker import (
    BackgroundJobExecutionError,
    BackgroundJobSnapshot,
)
from app.services.onboarding_initial_sync import InitialWorkspaceSyncHandler
from app.services.transactional_email_delivery import TransactionalEmailDeliveryHandler
from app.services.workspace_deletion_execution import WorkspaceDeletionJobHandler


def snapshot(
    *,
    job_type: BackgroundJobType = (BackgroundJobType.INCREMENTAL_SEASON_SYNC),
    workspace_id: int = 7,
    payload_version: int = 1,
    payload: dict | None = None,
) -> BackgroundJobSnapshot:
    return BackgroundJobSnapshot(
        id=42,
        workspace_id=workspace_id,
        job_type=job_type,
        payload_version=payload_version,
        payload=(
            payload
            if payload is not None
            else {
                "season_id": 31,
                "full_sync": False,
                "trigger": "administrator",
            }
        ),
        attempt_count=1,
        maximum_attempts=5,
    )


def test_incremental_payload_is_validated() -> None:
    season_id = validate_sync_job_payload(
        snapshot(),
        expected_full_sync=False,
    )

    assert season_id == 31


def test_full_sync_payload_is_validated() -> None:
    season_id = validate_sync_job_payload(
        snapshot(
            job_type=(BackgroundJobType.FULL_SEASON_SYNC),
            payload={
                "season_id": 31,
                "full_sync": True,
                "trigger": "administrator",
            },
        ),
        expected_full_sync=True,
    )

    assert season_id == 31


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "season_id": True,
        },
        {
            "season_id": 0,
        },
        {
            "season_id": "31",
        },
    ],
)
def test_invalid_season_payload_is_rejected(
    payload: dict,
) -> None:
    with pytest.raises(
        BackgroundJobExecutionError,
        match="payload is invalid",
    ):
        validate_sync_job_payload(
            snapshot(payload=payload),
            expected_full_sync=False,
        )


def test_unsupported_payload_version_is_rejected() -> None:
    with pytest.raises(
        BackgroundJobExecutionError,
        match="version is not supported",
    ):
        validate_sync_job_payload(
            snapshot(payload_version=2),
            expected_full_sync=False,
        )


def test_mismatched_sync_mode_is_rejected() -> None:
    with pytest.raises(
        BackgroundJobExecutionError,
        match="mode is invalid",
    ):
        validate_sync_job_payload(
            snapshot(
                payload={
                    "season_id": 31,
                    "full_sync": True,
                },
            ),
            expected_full_sync=False,
        )


@pytest.mark.parametrize(
    "field",
    [
        "encrypted_credentials",
        "espn_s2",
        "swid",
        "password",
        "oauth_access_token",
        "oauth_refresh_token",
        "session_token",
        "authorization",
        "provider_credentials",
    ],
)
def test_secret_fields_are_rejected(
    field: str,
) -> None:
    with pytest.raises(
        BackgroundJobExecutionError,
        match="prohibited data",
    ):
        reject_secret_payload_fields(
            {
                "season_id": 31,
                field: "secret-value",
            }
        )


def test_ownership_validation_accepts_active_season() -> None:
    session = MagicMock()
    workspace = SimpleNamespace(
        id=7,
        active_season_id=31,
    )
    season = SimpleNamespace(
        id=31,
        league=SimpleNamespace(
            installation_id=7,
        ),
    )
    session.get.side_effect = [
        workspace,
        season,
    ]

    validate_sync_job_ownership(
        session,
        workspace_id=7,
        season_id=31,
    )


def test_cross_workspace_season_is_rejected() -> None:
    session = MagicMock()
    session.get.side_effect = [
        SimpleNamespace(
            id=7,
            active_season_id=31,
        ),
        SimpleNamespace(
            id=31,
            league=SimpleNamespace(
                installation_id=8,
            ),
        ),
    ]

    with pytest.raises(
        BackgroundJobExecutionError,
        match="does not belong",
    ):
        validate_sync_job_ownership(
            session,
            workspace_id=7,
            season_id=31,
        )


def test_inactive_season_is_rejected() -> None:
    session = MagicMock()
    session.get.side_effect = [
        SimpleNamespace(
            id=7,
            active_season_id=99,
        ),
        SimpleNamespace(
            id=31,
            league=SimpleNamespace(
                installation_id=7,
            ),
        ),
    ]

    with pytest.raises(
        BackgroundJobExecutionError,
        match="active season",
    ):
        validate_sync_job_ownership(
            session,
            workspace_id=7,
            season_id=31,
        )


class RecordingSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    def commit(self) -> None:
        self.events.append("commit")

    def close(self) -> None:
        self.closed = True
        self.events.append("close")

    def __enter__(self) -> RecordingSession:
        self.events.append("open")

        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def test_handler_validates_before_synchronizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    session_factory = MagicMock(side_effect=lambda: RecordingSession(events))
    ownership_validator = MagicMock(
        side_effect=BackgroundJobExecutionError(
            safe_error_code="inactive_season_job",
            safe_error_message="The background job season is not the workspace's active season.",
        )
    )
    prepare = MagicMock()
    execute = MagicMock()

    monkeypatch.setattr(
        "app.services.background_job_handlers.validate_sync_job_ownership",
        ownership_validator,
    )
    monkeypatch.setattr(
        "app.services.background_job_handlers.prepare_active_season_sync",
        prepare,
    )
    monkeypatch.setattr(
        "app.services.background_job_handlers.execute_provider_sync",
        execute,
    )

    handler = SynchronizationBackgroundJobHandler(
        session_factory=session_factory,
        settings=SimpleNamespace(),
        full_sync=False,
        registry=MagicMock(),
    )

    with pytest.raises(BackgroundJobExecutionError) as raised:
        handler(snapshot())

    assert raised.value.safe_error_code == "inactive_season_job"
    prepare.assert_not_called()
    execute.assert_not_called()
    assert "commit" not in events


def test_handler_executes_after_preparation_session_is_committed_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    sessions: list[RecordingSession] = []

    def make_session() -> RecordingSession:
        session = RecordingSession(events)
        sessions.append(session)

        return session

    session_factory = MagicMock(side_effect=make_session)
    settings = SimpleNamespace()
    registry = MagicMock()
    plan = object()
    provider = object()
    ownership_validator = MagicMock()

    def prepare(**kwargs: object) -> tuple[object, object]:
        events.append("prepare")
        assert kwargs["session"] is sessions[0]

        return plan, provider

    prepare_mock = MagicMock(side_effect=prepare)

    def execute(**kwargs: object) -> None:
        events.append("execute")
        assert sessions[0].closed is True

    execute_mock = MagicMock(side_effect=execute)

    monkeypatch.setattr(
        "app.services.background_job_handlers.validate_sync_job_ownership",
        ownership_validator,
    )
    monkeypatch.setattr(
        "app.services.background_job_handlers.prepare_active_season_sync",
        prepare_mock,
    )
    monkeypatch.setattr(
        "app.services.background_job_handlers.execute_provider_sync",
        execute_mock,
    )

    job = snapshot()
    handler = SynchronizationBackgroundJobHandler(
        session_factory=session_factory,
        settings=settings,
        full_sync=False,
        registry=registry,
    )
    handler(job)

    assert events == ["open", "prepare", "commit", "close", "execute"]
    ownership_validator.assert_called_once_with(sessions[0], workspace_id=7, season_id=31)

    prepare_kwargs = prepare_mock.call_args.kwargs
    assert prepare_kwargs["workspace_id"] == 7
    assert prepare_kwargs["settings"] is settings
    assert prepare_kwargs["full_sync"] is False
    assert prepare_kwargs["registry"] is registry
    authority = prepare_kwargs["job_authority"]
    assert (authority.job_id, authority.attempt_count) == (job.id, job.attempt_count)

    execute_mock.assert_called_once_with(
        session_factory=session_factory,
        plan=plan,
        provider=provider,
    )


def test_handler_registry_contains_sync_deletion_and_email_jobs() -> None:
    registry = MagicMock()

    handlers = build_background_job_handlers(
        session_factory=MagicMock(),
        settings=SimpleNamespace(),
        registry=registry,
    )

    assert set(handlers) == {
        (BackgroundJobType.INCREMENTAL_SEASON_SYNC),
        BackgroundJobType.FULL_SEASON_SYNC,
        BackgroundJobType.INITIAL_WORKSPACE_SYNC,
        BackgroundJobType.WORKSPACE_DELETION,
        BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY,
    }
    assert isinstance(
        handlers[BackgroundJobType.TRANSACTIONAL_EMAIL_DELIVERY],
        TransactionalEmailDeliveryHandler,
    )
    assert isinstance(
        handlers[BackgroundJobType.WORKSPACE_DELETION],
        WorkspaceDeletionJobHandler,
    )
    assert isinstance(
        handlers[BackgroundJobType.INITIAL_WORKSPACE_SYNC],
        InitialWorkspaceSyncHandler,
    )

    incremental_handler = handlers[BackgroundJobType.INCREMENTAL_SEASON_SYNC]
    full_handler = handlers[BackgroundJobType.FULL_SEASON_SYNC]

    assert isinstance(
        incremental_handler,
        SynchronizationBackgroundJobHandler,
    )
    assert isinstance(
        full_handler,
        SynchronizationBackgroundJobHandler,
    )
    assert incremental_handler.registry is registry
    assert full_handler.registry is registry
