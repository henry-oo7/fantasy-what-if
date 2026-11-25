from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.db.platform_enums import BackgroundJobType
from app.services.background_job_handlers import (
    LegacySynchronizationJobAdapter,
    SeasonSynchronizationJobHandler,
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
from app.services.provider_sync import SyncJobAuthority
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


def season_snapshot(
    *,
    payload: dict | None = None,
    payload_version: int = 1,
    workspace_id: int | None = None,
) -> BackgroundJobSnapshot:
    return BackgroundJobSnapshot(
        id=42,
        workspace_id=workspace_id,
        job_type=BackgroundJobType.LEAGUE_SEASON_SYNC,
        payload_version=payload_version,
        payload=payload if payload is not None else {"version": 1, "season_id": 31},
        attempt_count=2,
        maximum_attempts=5,
    )


def test_season_handler_passes_season_and_job_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synchronize = MagicMock()
    monkeypatch.setattr("app.services.background_job_handlers.synchronize_season", synchronize)
    session_factory = MagicMock()
    settings = SimpleNamespace()
    registry = MagicMock()

    handler = SeasonSynchronizationJobHandler(
        session_factory=session_factory,
        settings=settings,
        registry=registry,
    )
    handler(season_snapshot())

    synchronize.assert_called_once_with(
        session_factory=session_factory,
        season_id=31,
        settings=settings,
        registry=registry,
        job_authority=SyncJobAuthority(job_id=42, attempt_count=2),
    )


@pytest.mark.parametrize(
    ("payload", "payload_version"),
    [
        ({}, 1),
        ({"version": 1}, 1),
        ({"version": 1, "season_id": 0}, 1),
        ({"version": 1, "season_id": True}, 1),
        ({"version": 1, "season_id": "31"}, 1),
        ({"version": 2, "season_id": 31}, 1),
        ({"version": 1, "season_id": 31, "workspace_id": 7}, 1),
        ({"version": 1, "season_id": 31, "full_sync": True}, 1),
        ({"version": 1, "season_id": 31}, 2),
    ],
)
def test_season_handler_rejects_invalid_payload_without_synchronizing(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
    payload_version: int,
) -> None:
    synchronize = MagicMock()
    monkeypatch.setattr("app.services.background_job_handlers.synchronize_season", synchronize)

    handler = SeasonSynchronizationJobHandler(
        session_factory=MagicMock(),
        settings=SimpleNamespace(),
    )

    with pytest.raises(BackgroundJobExecutionError) as raised:
        handler(season_snapshot(payload=payload, payload_version=payload_version))

    assert raised.value.safe_error_code == "invalid_job_payload"
    synchronize.assert_not_called()


def test_legacy_adapter_enqueues_season_job_and_never_synchronizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    sessions: list[RecordingSession] = []

    def make_session() -> RecordingSession:
        session = RecordingSession(events)
        sessions.append(session)

        return session

    ownership_validator = MagicMock(side_effect=lambda *a, **k: events.append("validate"))
    enqueue = MagicMock(side_effect=lambda *a, **k: events.append("enqueue"))
    synchronize = MagicMock()
    monkeypatch.setattr(
        "app.services.background_job_handlers.validate_sync_job_ownership", ownership_validator
    )
    monkeypatch.setattr("app.services.background_job_handlers.enqueue_season_sync", enqueue)
    monkeypatch.setattr("app.services.background_job_handlers.synchronize_season", synchronize)

    adapter = LegacySynchronizationJobAdapter(
        session_factory=MagicMock(side_effect=make_session),
        full_sync=False,
    )
    adapter(snapshot())

    assert events == ["open", "validate", "enqueue", "commit", "close"]
    ownership_validator.assert_called_once_with(sessions[0], workspace_id=7, season_id=31)
    enqueue.assert_called_once_with(sessions[0], season_id=31)
    synchronize.assert_not_called()


def test_legacy_full_adapter_accepts_full_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueue = MagicMock()
    monkeypatch.setattr(
        "app.services.background_job_handlers.validate_sync_job_ownership", MagicMock()
    )
    monkeypatch.setattr("app.services.background_job_handlers.enqueue_season_sync", enqueue)

    adapter = LegacySynchronizationJobAdapter(
        session_factory=MagicMock(side_effect=lambda: RecordingSession([])),
        full_sync=True,
    )
    adapter(
        snapshot(
            job_type=BackgroundJobType.FULL_SEASON_SYNC,
            payload={"season_id": 31, "full_sync": True, "trigger": "administrator"},
        )
    )

    assert enqueue.call_args.kwargs == {"season_id": 31}


def test_legacy_adapter_rejects_mode_mismatch_before_opening_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueue = MagicMock()
    session_factory = MagicMock()
    monkeypatch.setattr("app.services.background_job_handlers.enqueue_season_sync", enqueue)

    adapter = LegacySynchronizationJobAdapter(session_factory=session_factory, full_sync=False)

    with pytest.raises(BackgroundJobExecutionError, match="mode is invalid"):
        adapter(snapshot(payload={"season_id": 31, "full_sync": True}))

    session_factory.assert_not_called()
    enqueue.assert_not_called()


def test_legacy_adapter_rejects_missing_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueue = MagicMock()
    monkeypatch.setattr("app.services.background_job_handlers.enqueue_season_sync", enqueue)

    adapter = LegacySynchronizationJobAdapter(session_factory=MagicMock(), full_sync=False)
    job = snapshot()
    object.__setattr__(job, "workspace_id", None)

    with pytest.raises(BackgroundJobExecutionError) as raised:
        adapter(job)

    assert raised.value.safe_error_code == "workspace_unavailable"
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    ("workspace", "season", "code"),
    [
        (None, None, "workspace_unavailable"),
        (SimpleNamespace(id=7, active_season_id=31), None, "season_unavailable"),
        (
            SimpleNamespace(id=7, active_season_id=31),
            SimpleNamespace(id=31, league=SimpleNamespace(installation_id=8)),
            "workspace_ownership_mismatch",
        ),
        (
            SimpleNamespace(id=7, active_season_id=99),
            SimpleNamespace(id=31, league=SimpleNamespace(installation_id=7)),
            "inactive_season_job",
        ),
    ],
)
def test_legacy_adapter_ownership_errors_prevent_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    workspace: object,
    season: object,
    code: str,
) -> None:
    events: list[str] = []
    session = RecordingSession(events)
    session.get = MagicMock(side_effect=[workspace, season])  # type: ignore[attr-defined]
    enqueue = MagicMock()
    monkeypatch.setattr("app.services.background_job_handlers.enqueue_season_sync", enqueue)

    adapter = LegacySynchronizationJobAdapter(
        session_factory=MagicMock(return_value=session),
        full_sync=False,
    )

    with pytest.raises(BackgroundJobExecutionError) as raised:
        adapter(snapshot())

    assert raised.value.safe_error_code == code
    enqueue.assert_not_called()
    assert "commit" not in events


def test_handler_registry_contains_sync_deletion_and_email_jobs() -> None:
    registry = MagicMock()

    handlers = build_background_job_handlers(
        session_factory=MagicMock(),
        settings=SimpleNamespace(),
        registry=registry,
    )

    assert set(handlers) == {
        BackgroundJobType.LEAGUE_SEASON_SYNC,
        BackgroundJobType.INCREMENTAL_SEASON_SYNC,
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

    season_handler = handlers[BackgroundJobType.LEAGUE_SEASON_SYNC]
    incremental_handler = handlers[BackgroundJobType.INCREMENTAL_SEASON_SYNC]
    full_handler = handlers[BackgroundJobType.FULL_SEASON_SYNC]

    assert isinstance(season_handler, SeasonSynchronizationJobHandler)
    assert season_handler.registry is registry
    assert isinstance(incremental_handler, LegacySynchronizationJobAdapter)
    assert isinstance(full_handler, LegacySynchronizationJobAdapter)
    assert incremental_handler.full_sync is False
    assert full_handler.full_sync is True
