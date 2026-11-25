"""Routine contract tests for the provider-synchronization plan and helpers.

Mostly pure unit tests. Tests that need persistence use committed
``SyncGraph`` data in ``fantasy_what_if_test`` and synthetic adapters; no
network I/O is performed.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import DeclarativeBase, Session

from app.db.models import Season, SyncRun, SyncStatus
from app.db.platform_enums import (
    FantasyProvider,
    ProviderAccessMode,
    SeasonStatus,
    SeasonSyncState,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation, ProviderConnection
from app.db.session import SessionFactory
from app.providers.errors import (
    ProviderAuthenticationError,
    ProviderErrorContext,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from app.services.background_job_worker import BackgroundJobExecutionError
from app.services.provider_sync import (
    GENERIC_SYNC_FAILURE_MESSAGE,
    INVALID_PROVIDER_DATA_MESSAGE,
    ProviderFetchTarget,
    ProviderSyncPlan,
    SyncWorkspaceRequirement,
    credential_marker,
    prepare_season_sync,
    record_sync_failure,
    safe_sync_failure_message,
)
from app.services.provider_sync_policy import ProviderSyncValidationError
from tests.support.provider_sync_fixtures import (
    SyncGraph,
    SyntheticSeasonAdapter,
    forbid_live_network,
    graph_factory,
    registry_for,
    sync_runs,
    worker_settings,
)


@pytest.fixture
def make_graph() -> Iterator[Callable[[], SyncGraph]]:
    with graph_factory() as create:
        yield create


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


def adapter_for(graph: SyncGraph) -> SyntheticSeasonAdapter:
    return SyntheticSeasonAdapter(provider_league_id=graph.provider_league_id, dataset="alpha")


def prepare(
    graph: SyncGraph,
    **overrides: Any,
) -> ProviderSyncPlan:
    plan, _ = prepare_season_sync(
        session_factory=SessionFactory,
        season_id=graph.season_id,
        settings=worker_settings(),
        registry=registry_for(adapter_for(graph)),
        **overrides,
    )

    return plan


def season_of(graph: SyncGraph) -> Season:
    with SessionFactory() as session:
        season = session.get(Season, graph.season_id)
        assert season is not None
        session.expunge(season)
        return season


def mutate(graph: SyncGraph, *, workspace: dict | None = None, season: dict | None = None) -> None:
    with SessionFactory() as session:
        if workspace:
            session.execute(
                update(Installation)
                .where(Installation.id == graph.workspace_id)
                .values(**workspace)
            )
        if season:
            session.execute(update(Season).where(Season.id == graph.season_id).values(**season))
        session.commit()


# --------------------------------------------------------------------------
# a. plan shape
# --------------------------------------------------------------------------

ALLOWED_SCALARS = (int, str, bool, type(None), Enum)


def assert_detached_values(instance: object) -> None:
    for field in dataclasses.fields(instance):  # type: ignore[arg-type]
        value = getattr(instance, field.name)

        assert not isinstance(value, Session), field.name
        assert not isinstance(value, DeclarativeBase), field.name
        assert "_sa_instance_state" not in getattr(value, "__dict__", {}), field.name

        if dataclasses.is_dataclass(value):
            assert type(value).__dataclass_params__.frozen, field.name  # type: ignore[attr-defined]
            assert_detached_values(value)
        else:
            assert isinstance(value, ALLOWED_SCALARS), (field.name, type(value))


def test_plan_contains_only_detached_plain_values(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()

    plan = prepare(graph)

    assert isinstance(plan.target, ProviderFetchTarget)
    assert_detached_values(plan)
    # Transitional workspace fallback: the workspace's own connection.
    assert plan.legacy_workspace_id == graph.workspace_id
    assert plan.season_id == graph.season_id
    assert plan.provider_connection_id == graph.connection_id
    assert plan.access_mode is ProviderAccessMode.PRIVATE
    assert plan.expected_data_version == 0
    assert plan.target.full_sync is True
    season = season_of(graph)
    assert season.sync_state is SeasonSyncState.SYNCING
    assert season.last_sync_attempt_at is not None


def test_plan_and_target_are_frozen(
    make_graph: Callable[[], SyncGraph],
) -> None:
    plan = prepare(make_graph())

    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.season_id = 0  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.target.provider_league_id = "other"  # type: ignore[misc]


def test_plan_repr_omits_credential_marker_and_provider_league_id(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    plan = prepare(graph)

    rendered = repr(plan)

    assert plan.credential_marker not in rendered
    assert graph.provider_league_id not in rendered
    assert plan.credential_marker not in repr(plan.target)
    assert graph.swid not in rendered
    assert graph.espn_s2 not in rendered


# --------------------------------------------------------------------------
# b. credential marker
# --------------------------------------------------------------------------

CIPHERTEXT = "gAAAAABsyntheticCiphertextValueForMarkerTests0123456789abcdef"


def transient_connection(**overrides: Any) -> ProviderConnection:
    values: dict[str, Any] = {
        "id": 7,
        "installation_id": 1,
        "provider": FantasyProvider.ESPN,
        "display_name": "Synthetic connection",
        "encrypted_credentials": CIPHERTEXT,
        "credential_version": 1,
        "credential_schema_version": 1,
        "is_active": True,
    }
    values.update(overrides)

    return ProviderConnection(**values)


def test_credential_marker_is_deterministic() -> None:
    assert credential_marker(transient_connection()) == credential_marker(transient_connection())


@pytest.mark.parametrize(
    "override",
    [
        {"encrypted_credentials": CIPHERTEXT + "x"},
        {"credential_version": 2},
        {"credential_schema_version": 2},
        {"id": 8},
    ],
    ids=["ciphertext", "credential_version", "schema_version", "id"],
)
def test_credential_marker_changes_with_connection_values(override: dict[str, Any]) -> None:
    assert credential_marker(transient_connection(**override)) != credential_marker(
        transient_connection()
    )


def test_credential_marker_is_hex_digest_without_ciphertext() -> None:
    marker = credential_marker(transient_connection())

    assert re.fullmatch(r"[0-9a-f]{64}", marker)

    for start in range(0, len(CIPHERTEXT) - 8):
        assert CIPHERTEXT[start : start + 8] not in marker


def test_credential_marker_tolerates_missing_ciphertext() -> None:
    marker = credential_marker(transient_connection(encrypted_credentials=None))

    assert re.fullmatch(r"[0-9a-f]{64}", marker)


# --------------------------------------------------------------------------
# c. safe failure messages
# --------------------------------------------------------------------------


def provider_error(error_type: type[Exception]) -> Exception:
    return error_type(
        context=ProviderErrorContext(
            provider=FantasyProvider.ESPN, operation="fetch_league", retryable=True
        ),
        safe_message="SECRET-DETAIL upstream response",
    )  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "error_type",
    [ProviderAuthenticationError, ProviderUnavailableError, ProviderRateLimitError],
)
def test_provider_errors_map_to_class_default_message(error_type: type[Exception]) -> None:
    error = provider_error(error_type)

    message = safe_sync_failure_message(error)

    assert message == error_type.default_safe_message  # type: ignore[attr-defined]
    assert "SECRET-DETAIL" not in message


def test_job_execution_error_maps_to_its_safe_message() -> None:
    error = BackgroundJobExecutionError(
        safe_error_code="workspace_unavailable",
        safe_error_message="The background job workspace is unavailable.",
    )

    assert safe_sync_failure_message(error) == "The background job workspace is unavailable."


def test_validation_error_maps_to_invalid_data_message() -> None:
    message = safe_sync_failure_message(ProviderSyncValidationError("SECRET-DETAIL team 9"))

    assert message == INVALID_PROVIDER_DATA_MESSAGE
    assert message == "The provider returned invalid synchronization data."


def test_unknown_error_maps_to_generic_message() -> None:
    message = safe_sync_failure_message(RuntimeError("SECRET"))

    assert message == GENERIC_SYNC_FAILURE_MESSAGE
    assert message == "Provider synchronization failed."


# --------------------------------------------------------------------------
# d. workspace lifecycle at preparation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("workspace", "season", "code"),
    [
        (
            {
                "registration_status": WorkspaceRegistrationStatus.DELETION_PENDING,
                "deletion_requested_at": datetime.now(UTC),
                "deletion_scheduled_for": datetime.now(UTC) + timedelta(days=30),
            },
            None,
            "workspace_unavailable",
        ),
        (
            {"registration_status": WorkspaceRegistrationStatus.SUSPENDED},
            None,
            "workspace_unavailable",
        ),
        (
            {"registration_status": WorkspaceRegistrationStatus.PROVISIONAL},
            None,
            "workspace_unavailable",
        ),
        ({"active_season_id": None}, None, "inactive_season_job"),
        (None, {"status": SeasonStatus.COMPLETED}, "inactive_season_job"),
    ],
    ids=["deletion_pending", "suspended", "provisional", "no_active_season", "completed_season"],
)
def test_prepare_refuses_ineligible_workspace_without_creating_run(
    make_graph: Callable[[], SyncGraph],
    workspace: dict | None,
    season: dict | None,
    code: str,
) -> None:
    graph = make_graph()
    runs_before = len(sync_runs(graph.season_id))
    mutate(graph, workspace=workspace, season=season)

    with pytest.raises(BackgroundJobExecutionError) as raised:
        prepare(
            graph,
            requirement=SyncWorkspaceRequirement.ACTIVE_SEASON,
        )

    assert raised.value.safe_error_code == code
    assert len(sync_runs(graph.season_id)) == runs_before
    # The attempt never started, but the Season records why, safely.
    season = season_of(graph)
    assert season.sync_state is SeasonSyncState.FAILED
    assert season.last_sync_failure_code == code
    assert season.data_version == 0


def test_prepare_accepts_active_workspace_and_records_running_run(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    runs_before = len(sync_runs(graph.season_id))

    plan = prepare(graph, requirement=SyncWorkspaceRequirement.ACTIVE_SEASON)

    runs = sync_runs(graph.season_id)
    assert len(runs) == runs_before + 1
    assert runs[-1].id == plan.sync_run_id
    assert runs[-1].status is SyncStatus.RUNNING


def test_initial_onboarding_requirement_refuses_active_workspace(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    runs_before = len(sync_runs(graph.season_id))

    with pytest.raises(BackgroundJobExecutionError) as raised:
        prepare(
            graph,
            requirement=SyncWorkspaceRequirement.INITIAL_ONBOARDING,
        )

    assert raised.value.safe_error_code == "onboarding_state_invalid"
    assert len(sync_runs(graph.season_id)) == runs_before


def test_prepare_refuses_mismatched_credential_marker(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    runs_before = len(sync_runs(graph.season_id))

    with pytest.raises(BackgroundJobExecutionError) as raised:
        prepare(
            graph,
            expected_credential_marker="mismatch",
        )

    assert raised.value.safe_error_code == "provider_connection_changed"
    assert raised.value.retryable is True
    assert len(sync_runs(graph.season_id)) == runs_before


# --------------------------------------------------------------------------
# e. failure bookkeeping
# --------------------------------------------------------------------------


def set_run_status(run_id: int, status: SyncStatus) -> None:
    with SessionFactory() as session:
        session.execute(update(SyncRun).where(SyncRun.id == run_id).values(status=status))
        session.commit()


def test_record_failure_marks_running_run_failed_and_truncates_message(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    plan = prepare(graph)

    record_sync_failure(
        SessionFactory,
        plan=plan,
        error=BackgroundJobExecutionError(
            safe_error_code="synthetic_failure", safe_error_message="x" * 1500
        ),
    )

    run = next(run for run in sync_runs(graph.season_id) if run.id == plan.sync_run_id)
    assert run.status is SyncStatus.FAILED
    assert run.completed_at is not None
    assert run.safe_error_message == "x" * 1000
    assert run.analytics_rebuilt is False
    season = season_of(graph)
    assert season.sync_state is SeasonSyncState.FAILED
    assert season.last_sync_failure_code == "synthetic_failure"
    assert season.data_version == 0


def test_record_failure_leaves_succeeded_run_untouched(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    plan = prepare(graph)
    set_run_status(plan.sync_run_id, SyncStatus.SUCCEEDED)

    record_sync_failure(SessionFactory, plan=plan, error=RuntimeError("late failure"))

    run = next(run for run in sync_runs(graph.season_id) if run.id == plan.sync_run_id)
    assert run.status is SyncStatus.SUCCEEDED
    assert run.safe_error_message is None


def test_record_failure_never_raises_when_factory_fails() -> None:
    def broken_factory() -> Session:
        raise RuntimeError("database unavailable")

    plan = ProviderSyncPlan(
        season_id=1,
        league_id=1,
        season_year=2026,
        provider=FantasyProvider.ESPN,
        access_mode=ProviderAccessMode.PUBLIC,
        provider_connection_id=None,
        credential_marker=None,
        legacy_workspace_id=None,
        requirement=SyncWorkspaceRequirement.ACTIVE_SEASON,
        expected_data_version=0,
        sync_run_id=1,
        target=ProviderFetchTarget(
            provider_league_id="1", provider_season_id="2026", full_sync=True
        ),
    )

    record_sync_failure(broken_factory, plan=plan, error=RuntimeError("boom"))
