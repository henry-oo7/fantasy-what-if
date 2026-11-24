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


def test_handler_validates_before_synchronizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_session = MagicMock()
    session_context = MagicMock()
    session_context.__enter__.return_value = database_session
    session_factory = MagicMock(return_value=session_context)
    settings = SimpleNamespace()
    ownership_validator = MagicMock()
    synchronize = MagicMock()

    monkeypatch.setattr(
        ("app.services.background_job_handlers.validate_sync_job_ownership"),
        ownership_validator,
    )
    monkeypatch.setattr(
        ("app.services.background_job_handlers.synchronize_active_season"),
        synchronize,
    )

    registry = MagicMock()

    handler = SynchronizationBackgroundJobHandler(
        session_factory=session_factory,
        settings=settings,
        full_sync=False,
        registry=registry,
    )
    handler(snapshot())

    ownership_validator.assert_called_once_with(
        database_session,
        workspace_id=7,
        season_id=31,
    )
    synchronize.assert_called_once_with(
        session=database_session,
        workspace_id=7,
        settings=settings,
        full_sync=False,
        registry=registry,
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
