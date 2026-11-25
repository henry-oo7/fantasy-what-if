"""Routine credential-replacement behavior on committed PostgreSQL data.

Runs only against ``fantasy_what_if_test``. Rows are committed through
``tests/support/provider_sync_fixtures.py`` and cleaned up afterwards; the
provider is a synthetic in-process fake and no network is used.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, update

from app.db.models import Season
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import Installation, ProviderConnection, ProviderLeagueClaim
from app.db.session import SessionFactory
from app.db.session import engine as default_engine
from app.providers.credential_keyring import ProviderCredentialKeyring
from app.providers.credential_vault import ProviderCredentialVault
from app.providers.domain import ProviderCredentials, ProviderValidationResult
from app.providers.registry import ProviderRegistry
from app.services import provider_credential_replacement as replacement_module
from app.services.provider_credential_replacement import (
    ProviderCredentialReplacementUnavailableError,
    ProviderCredentialReplacementValidationError,
    replace_provider_credentials,
)
from tests.support.provider_sync_fixtures import (
    KEY,
    SyncGraph,
    forbid_live_network,
    graph_factory,
)

NOW = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
NEW_SWID = "{SYNTH-REPLACEMENT-SWID}"
NEW_S2 = "SYNTH-REPLACEMENT-ESPN-S2"
ATTACKER_LEAGUE = "attacker-controlled-league"
ATTACKER_SEASON = "1900"


class FakeProvider:
    provider = FantasyProvider.ESPN

    def __init__(
        self,
        *,
        league_id: str,
        is_valid: bool = True,
        returned_league_id: str | None = None,
        returned_season_id: str = "2026",
    ) -> None:
        self.is_valid = is_valid
        self.returned_league_id = returned_league_id or league_id
        self.returned_season_id = returned_season_id
        self.fetch_calls: list[tuple[str, str]] = []

    def validate_configuration(self) -> ProviderValidationResult:
        return ProviderValidationResult(
            is_valid=self.is_valid,
            provider=self.provider,
            safe_message=(
                "ESPN configuration is valid."
                if self.is_valid
                else "The fantasy provider credentials were rejected."
            ),
        )

    def fetch_league(self, *, provider_league_id: str, provider_season_id: str):
        self.fetch_calls.append((provider_league_id, provider_season_id))

        return SimpleNamespace(
            provider_league_id=self.returned_league_id,
            provider_season_id=self.returned_season_id,
        )

    def discover_leagues(self):
        raise AssertionError("Credential replacement must not use league discovery.")

    def fetch_matchups(self, **_):
        raise AssertionError("Credential replacement must not synchronize matchups.")

    def fetch_weekly_rosters(self, **_):
        raise AssertionError("Credential replacement must not synchronize rosters.")


class Registry:
    """Records adapter creation and the credentials handed to the factory."""

    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider
        self.created = 0
        self.captured: dict[str, str] = {}
        self.registry = ProviderRegistry()
        self.registry.register(FantasyProvider.ESPN, self._create)

    def _create(self, credentials: ProviderCredentials) -> FakeProvider:
        self.created += 1
        self.captured.update(credentials.values)

        return self.provider


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_live_network(monkeypatch)


@pytest.fixture
def make_graph() -> Iterator[Callable[[], SyncGraph]]:
    with graph_factory() as create:
        yield create


def keyring(
    *,
    current_version: int = 1,
    historical: dict[int, str] | None = None,
    current_key: str = KEY,
) -> ProviderCredentialKeyring:
    keys = dict(historical or {})
    keys[current_version] = current_key

    return ProviderCredentialKeyring(keys=keys, current_version=current_version)


def submitted() -> ProviderCredentials:
    return ProviderCredentials(
        values={
            "league_id": ATTACKER_LEAGUE,
            "season": ATTACKER_SEASON,
            "espn_s2": NEW_S2,
            "swid": NEW_SWID,
        }
    )


def replace(graph: SyncGraph, registry: Registry, ring: ProviderCredentialKeyring | None = None):
    return replace_provider_credentials(
        SessionFactory,
        workspace_id=graph.workspace_id,
        submitted_credentials=submitted(),
        credential_keyring=ring or keyring(),
        registry=registry.registry,
        now=NOW,
    )


def connection_state(graph: SyncGraph) -> dict[str, Any]:
    with SessionFactory() as session:
        connection = session.get(ProviderConnection, graph.connection_id)
        assert connection is not None

        return {
            "encrypted_credentials": connection.encrypted_credentials,
            "credential_version": connection.credential_version,
            "credential_schema_version": connection.credential_schema_version,
            "last_validated_at": connection.last_validated_at,
            "last_validation_error": connection.last_validation_error,
        }


def decrypt_stored(graph: SyncGraph, keys: dict[int, str]) -> dict[str, str]:
    state = connection_state(graph)
    envelope = ProviderCredentialVault(keys=keys).decrypt(
        encrypted_credentials=state["encrypted_credentials"],
        key_version=state["credential_version"],
        expected_provider=FantasyProvider.ESPN,
        expected_schema_version=1,
    )

    return dict(envelope.credentials.values)


def set_workspace(graph: SyncGraph, **values: Any) -> None:
    with SessionFactory() as session:
        session.execute(
            update(Installation).where(Installation.id == graph.workspace_id).values(**values)
        )
        session.commit()


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


def test_replacement_validates_identity_before_atomic_update(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = connection_state(graph)
    provider = FakeProvider(league_id=graph.provider_league_id)
    registry = Registry(provider)

    result = replace(graph, registry)

    # Stored league and season override the attacker-supplied values.
    assert registry.captured["league_id"] == graph.provider_league_id
    assert registry.captured["season"] == "2026"
    assert registry.captured["espn_s2"] == NEW_S2
    assert registry.captured["swid"] == NEW_SWID
    assert ATTACKER_LEAGUE not in registry.captured.values()
    assert ATTACKER_SEASON not in registry.captured.values()
    assert provider.fetch_calls == [(graph.provider_league_id, "2026")]

    after = connection_state(graph)
    assert after["encrypted_credentials"] != before["encrypted_credentials"]
    assert after["credential_version"] == 1
    assert after["credential_schema_version"] == 1
    assert after["last_validated_at"] == NOW
    assert after["last_validation_error"] is None

    values = decrypt_stored(graph, {1: KEY})
    assert values == registry.captured
    assert values["swid"] == NEW_SWID
    assert values["espn_s2"] == NEW_S2

    assert result.workspace_id == graph.workspace_id
    assert result.connection_id == graph.connection_id
    assert result.provider == "espn"
    assert result.provider_league_id == graph.provider_league_id
    assert result.provider_season_id == "2026"
    assert result.credential_version == 1
    assert result.validated_at == NOW


def test_replacement_clears_previous_validation_error(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()

    with SessionFactory() as session:
        session.execute(
            update(ProviderConnection)
            .where(ProviderConnection.id == graph.connection_id)
            .values(last_validation_error="Previous synthetic failure.")
        )
        session.commit()

    replace(graph, Registry(FakeProvider(league_id=graph.provider_league_id)))

    assert connection_state(graph)["last_validation_error"] is None


# --------------------------------------------------------------------------
# Validation failures keep the stored credentials
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider_kwargs", "message"),
    [
        ({"is_valid": False}, "credentials were rejected"),
        ({"returned_league_id": "different-league"}, "do not access the claimed league"),
        ({"returned_season_id": "2025"}, "do not access the active season"),
    ],
    ids=["invalid-credentials", "different-league", "different-season"],
)
def test_failed_validation_preserves_existing_envelope(
    make_graph: Callable[[], SyncGraph],
    provider_kwargs: dict[str, Any],
    message: str,
) -> None:
    graph = make_graph()
    before = connection_state(graph)
    registry = Registry(FakeProvider(league_id=graph.provider_league_id, **provider_kwargs))

    with pytest.raises(ProviderCredentialReplacementValidationError, match=message):
        replace(graph, registry)

    assert connection_state(graph) == before
    assert decrypt_stored(graph, {1: KEY})["swid"] == graph.swid


# --------------------------------------------------------------------------
# Unavailable context
# --------------------------------------------------------------------------


def _delete_claim(graph: SyncGraph, other: SyncGraph) -> None:
    with SessionFactory() as session:
        session.execute(
            delete(ProviderLeagueClaim).where(
                ProviderLeagueClaim.workspace_id == graph.workspace_id
            )
        )
        session.commit()


def _claim_of_other_workspace(graph: SyncGraph, other: SyncGraph) -> None:
    with SessionFactory() as session:
        session.execute(
            update(ProviderLeagueClaim)
            .where(ProviderLeagueClaim.workspace_id == graph.workspace_id)
            .values(workspace_id=other.workspace_id)
        )
        session.commit()


@pytest.mark.parametrize(
    "mutate",
    [_delete_claim, _claim_of_other_workspace],
    ids=["missing-claim", "claim-in-other-workspace"],
)
def test_claim_must_belong_to_active_workspace_and_league(
    make_graph: Callable[[], SyncGraph],
    mutate: Callable[[SyncGraph, SyncGraph], None],
) -> None:
    graph = make_graph()
    other = make_graph()
    with SessionFactory() as session:
        session.execute(
            delete(ProviderLeagueClaim).where(
                ProviderLeagueClaim.workspace_id == other.workspace_id
            )
        )
        session.commit()
    before = connection_state(graph)
    registry = Registry(FakeProvider(league_id=graph.provider_league_id))

    mutate(graph, other)

    with pytest.raises(ProviderCredentialReplacementUnavailableError, match="claim is unavailable"):
        replace(graph, registry)

    assert registry.created == 0
    assert connection_state(graph) == before


def test_missing_active_context_is_rejected(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = connection_state(graph)
    registry = Registry(FakeProvider(league_id=graph.provider_league_id))
    set_workspace(graph, active_season_id=None)

    with pytest.raises(
        ProviderCredentialReplacementUnavailableError,
        match="does not have an active provider season",
    ):
        replace(graph, registry)

    assert registry.created == 0
    assert connection_state(graph) == before


def test_nonexistent_workspace_is_rejected(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    registry = Registry(FakeProvider(league_id=graph.provider_league_id))

    with pytest.raises(ProviderCredentialReplacementUnavailableError, match="does not exist"):
        replace_provider_credentials(
            SessionFactory,
            workspace_id=graph.workspace_id + 1_000_000_000,
            submitted_credentials=submitted(),
            credential_keyring=keyring(),
            registry=registry.registry,
        )

    assert registry.created == 0


# --------------------------------------------------------------------------
# Lifecycle preparation
# --------------------------------------------------------------------------


def _deletion_pending() -> dict[str, Any]:
    requested_at = datetime.now(UTC)

    return {
        "registration_status": WorkspaceRegistrationStatus.DELETION_PENDING,
        "deletion_requested_at": requested_at,
        "deletion_scheduled_for": requested_at + timedelta(days=30),
    }


@pytest.mark.parametrize(
    "lifecycle",
    [
        _deletion_pending(),
        {"registration_status": WorkspaceRegistrationStatus.SUSPENDED},
        {"registration_status": WorkspaceRegistrationStatus.PROVISIONAL},
        {"status": InstallationStatus.SETUP_IN_PROGRESS},
    ],
    ids=["deletion-pending", "suspended", "provisional", "setup-in-progress"],
)
def test_inactive_workspace_is_rejected_during_preparation(
    make_graph: Callable[[], SyncGraph],
    lifecycle: dict[str, Any],
) -> None:
    graph = make_graph()
    before = connection_state(graph)
    registry = Registry(FakeProvider(league_id=graph.provider_league_id))
    set_workspace(graph, **lifecycle)

    with pytest.raises(
        ProviderCredentialReplacementUnavailableError, match="The workspace is not active."
    ):
        replace(graph, registry)

    assert registry.created == 0
    assert connection_state(graph) == before


# --------------------------------------------------------------------------
# Key versions
# --------------------------------------------------------------------------


def test_replacement_upgrades_to_current_key_version(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    second_key = _new_key()
    registry = Registry(FakeProvider(league_id=graph.provider_league_id))

    result = replace(
        graph,
        registry,
        keyring(current_version=2, historical={1: KEY}, current_key=second_key),
    )

    assert result.credential_version == 2
    assert connection_state(graph)["credential_version"] == 2
    values = decrypt_stored(graph, {1: KEY, 2: second_key})
    assert values["espn_s2"] == NEW_S2
    assert values["swid"] == NEW_SWID


def _new_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


# --------------------------------------------------------------------------
# Detached preparation plan
# --------------------------------------------------------------------------


def test_preparation_plan_is_detached_and_holds_no_connection(
    make_graph: Callable[[], SyncGraph],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = make_graph()
    plans: list[Any] = []

    def capture(provider: Any, plan: Any) -> None:
        assert default_engine.pool.checkedout() == 0
        plans.append(plan)

    monkeypatch.setattr(replacement_module, "_validate_with_provider", capture)
    replace(graph, Registry(FakeProvider(league_id=graph.provider_league_id)))

    [plan] = plans
    assert dataclasses.is_dataclass(plan)

    for field in dataclasses.fields(plan):
        value = getattr(plan, field.name)
        assert isinstance(value, int | str | enum.Enum), field.name

    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.workspace_id = 0

    with SessionFactory() as session:
        season = session.get(Season, graph.season_id)
        assert season is not None
        assert plan.season_id == graph.season_id
        assert plan.provider_season_id == season.provider_season_id

    assert plan.workspace_id == graph.workspace_id
    assert plan.connection_id == graph.connection_id
    assert plan.credential_marker

    rendered = repr(plan)
    for secret in (
        graph.provider_league_id,
        plan.credential_marker,
        graph.swid,
        graph.espn_s2,
        NEW_SWID,
        NEW_S2,
    ):
        assert secret not in rendered
