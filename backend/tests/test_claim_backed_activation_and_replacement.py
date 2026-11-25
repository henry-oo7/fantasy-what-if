# ruff: noqa: F811  (imported pytest fixtures are used as test parameters)
"""Workspace activation and credential replacement are claim-backed.

``League.installation_id`` alone never authorizes either operation. Each
test starts from a ready workspace that succeeds, applies exactly one
mutation to that workspace's ``ProviderLeagueClaim`` (committed through a
separate Session), and proves the operation is denied before any change.

Runs only against ``fantasy_what_if_test`` with synthetic rows and an
in-process fake provider; no network is used.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.admin_dependencies import get_provider_credential_replacer
from app.api.auth_dependencies import (
    get_current_user,
    get_current_workspace_id,
    require_session_csrf,
)
from app.db.auth_models import User, UserRole
from app.db.league_access_models import UserLeagueSeasonAccess
from app.db.models import League
from app.db.platform_enums import (
    FantasyProvider,
    InstallationStatus,
    OnboardingStage,
    UserSeasonProofMode,
    WorkspaceRegistrationStatus,
)
from app.db.platform_models import (
    Installation,
    ProviderLeagueClaim,
)
from app.db.session import SessionFactory
from app.main import app
from app.repositories.auth_repository import AuthenticationRepository
from app.repositories.league_access_repository import LeagueAccessRepository
from app.services.provider_credential_replacement import (
    ProviderCredentialReplacementUnavailableError,
    replace_provider_credentials,
)
from app.services.workspace_activation import (
    ActivationCategory,
    ActivationNotReadyError,
    activate_workspace,
    read_activation_readiness,
)
from tests.api.test_onboarding_activation import (  # noqa: F401 - fixtures
    PROVISIONAL_UNCHANGED,
    TRUSTED_ORIGIN,
    Seeded,
    activate,
    forbid_side_effects,
    lifecycle,
    real_authentication,
    review,
    seed,
)
from tests.support.provider_sync_fixtures import SyncGraph
from tests.test_provider_credential_replacement import (  # noqa: F401 - fixtures
    FakeProvider,
    Registry,
    connection_state,
    keyring,
    make_graph,
    no_live_network,
    replace,
)

MUTATIONS = [
    "missing-claim",
    "provider-mismatch",
    "normalized-id-mismatch",
    "other-workspace-claim",
    "personal-access-only",
]


@dataclass
class Extras:
    """Synthetic rows a mutation created beyond the ready workspace."""

    installation_ids: list[int] = field(default_factory=list)
    user_ids: list[int] = field(default_factory=list)
    other_workspace_id: int | None = None
    other_slug: str | None = None
    claim_league_identity: str | None = None


@pytest.fixture
def extras() -> Iterator[Extras]:
    """Declared after the workspace fixtures, so it is torn down first."""

    created = Extras()

    yield created

    with SessionFactory() as session:
        session.execute(
            delete(UserLeagueSeasonAccess).where(
                UserLeagueSeasonAccess.user_id.in_(created.user_ids)
            )
        )
        session.execute(
            delete(ProviderLeagueClaim).where(
                ProviderLeagueClaim.workspace_id.in_(created.installation_ids)
            )
        )
        session.execute(delete(User).where(User.id.in_(created.user_ids)))
        session.execute(delete(Installation).where(Installation.id.in_(created.installation_ids)))
        session.commit()


def mutate_claim(mutation: str, *, workspace_id: int, season_id: int, extras: Extras) -> None:
    """Apply exactly one claim mutation to the ready workspace, then commit."""

    suffix = uuid4().hex[:10]

    with SessionFactory() as session:
        claim = session.scalar(
            select(ProviderLeagueClaim).where(ProviderLeagueClaim.workspace_id == workspace_id)
        )
        assert claim is not None
        extras.claim_league_identity = claim.normalized_provider_league_id

        if mutation == "missing-claim":
            session.delete(claim)
        elif mutation == "provider-mismatch":
            claim.provider = FantasyProvider.SLEEPER
        elif mutation == "normalized-id-mismatch":
            different = f"different-{suffix}"
            claim.provider_league_id = different
            claim.normalized_provider_league_id = different
        elif mutation == "other-workspace-claim":
            other = Installation(
                application_name="Claim Holder Synthetic",
                slug=f"claim-holder-{suffix}",
                normalized_slug=f"claim-holder-{suffix}",
                timezone="America/Chicago",
                status=InstallationStatus.SETUP_IN_PROGRESS,
                registration_status=WorkspaceRegistrationStatus.PROVISIONAL,
                onboarding_stage=OnboardingStage.COMPLETE,
            )
            session.add(other)
            session.flush()
            extras.installation_ids.append(other.id)
            extras.other_workspace_id = other.id
            extras.other_slug = other.slug
            claim.workspace_id = other.id
        elif mutation == "personal-access-only":
            session.delete(claim)
            session.flush()
            user = AuthenticationRepository(session).create_user(
                username=f"personal-only-{suffix}",
                display_name="Personal Access Only",
                email=f"personal-only-{suffix}@example.com",
                password="correct horse battery staple",
                role=UserRole.ADMIN,
                workspace_id=None,
            )
            session.flush()
            extras.user_ids.append(user.id)
            LeagueAccessRepository(session).create_or_get_access(
                user_id=user.id,
                season_id=season_id,
                proof_mode=UserSeasonProofMode.PUBLIC,
                provider_connection_id=None,
                verified_at=datetime.now(UTC),
            )
        else:
            raise AssertionError(f"Unknown mutation {mutation}")

        session.commit()


def assert_no_claim_owner_detail(text: str, extras: Extras, *others: str) -> None:
    forbidden = [
        value for value in (extras.other_slug, extras.claim_league_identity, *others) if value
    ]

    for value in forbidden:
        assert value not in text

    if extras.other_workspace_id is not None:
        assert re.search(rf"\b{extras.other_workspace_id}\b", text) is None


# --------------------------------------------------------------------------
# Activation
# --------------------------------------------------------------------------


def administrator_id(workspace_id: int) -> int:
    with SessionFactory() as session:
        return session.scalar(
            select(User.id).where(User.workspace_id == workspace_id, User.role == UserRole.ADMIN)
        )


def test_activation_control_ready_workspace_activates(seed: Callable[..., Seeded]) -> None:
    seeded = seed()

    with SessionFactory() as session:
        readiness = read_activation_readiness(
            session, user_id=administrator_id(seeded.workspace_id), workspace_id=seeded.workspace_id
        )
    assert readiness.blocking_category is None

    assert activate(seeded.administrator).status_code == status.HTTP_200_OK


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_activation_is_denied_without_the_workspaces_own_claim(
    seed: Callable[..., Seeded], extras: Extras, mutation: str
) -> None:
    seeded = seed()
    user_id = administrator_id(seeded.workspace_id)

    with SessionFactory() as session:
        assert (
            read_activation_readiness(
                session, user_id=user_id, workspace_id=seeded.workspace_id
            ).blocking_category
            is None
        )

    assert lifecycle(seeded.workspace_id) == PROVISIONAL_UNCHANGED
    mutate_claim(
        mutation,
        workspace_id=seeded.workspace_id,
        season_id=seeded.internal_season_id,
        extras=extras,
    )

    # League.installation_id still equals the workspace: only the claim is gone or wrong.
    with SessionFactory() as session:
        league = session.get(League, seeded.internal_league_id)
        assert league is not None
        assert league.installation_id == seeded.workspace_id

    with SessionFactory() as session, pytest.raises(ActivationNotReadyError) as raised:
        activate_workspace(session, user_id=user_id, workspace_id=seeded.workspace_id)

    assert raised.value.category is ActivationCategory.PROVIDER_CONNECTION
    assert_no_claim_owner_detail(str(raised.value) + raised.value.detail, extras)
    assert lifecycle(seeded.workspace_id) == PROVISIONAL_UNCHANGED

    with SessionFactory() as session:
        readiness = read_activation_readiness(
            session, user_id=user_id, workspace_id=seeded.workspace_id
        )
    assert readiness.blocking_category is ActivationCategory.PROVIDER_CONNECTION


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_activation_api_denial_is_bounded_and_changes_nothing(
    seed: Callable[..., Seeded], extras: Extras, mutation: str
) -> None:
    seeded = seed()
    assert review(seeded.administrator).json()["ready"] is True

    mutate_claim(
        mutation,
        workspace_id=seeded.workspace_id,
        season_id=seeded.internal_season_id,
        extras=extras,
    )

    response = activate(seeded.administrator)

    assert response.status_code == status.HTTP_409_CONFLICT
    body = response.json()
    assert body["code"] == "activation_not_ready"
    assert body["category"] == "provider_connection"
    assert set(body) == {"detail", "code", "category", "retryable"}
    assert_no_claim_owner_detail(response.text, extras)
    assert lifecycle(seeded.workspace_id) == PROVISIONAL_UNCHANGED

    summary = review(seeded.administrator).json()
    assert summary["ready"] is False
    assert summary["blocking_category"] == "provider_connection"
    # The review legitimately shows the workspace's own connection League ID;
    # only another workspace's identity must be absent.
    assert_no_claim_owner_detail(
        str(summary),
        Extras(other_workspace_id=extras.other_workspace_id, other_slug=extras.other_slug),
    )


# --------------------------------------------------------------------------
# Credential replacement
# --------------------------------------------------------------------------


def test_replacement_control_ready_workspace_replaces(
    make_graph: Callable[[], SyncGraph],
) -> None:
    graph = make_graph()
    before = connection_state(graph)

    replace(graph, Registry(FakeProvider(league_id=graph.provider_league_id)))

    assert connection_state(graph)["encrypted_credentials"] != before["encrypted_credentials"]


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_replacement_is_denied_without_the_workspaces_own_claim(
    make_graph: Callable[[], SyncGraph], extras: Extras, mutation: str
) -> None:
    graph = make_graph()
    control = Registry(FakeProvider(league_id=graph.provider_league_id))
    replace(graph, control)
    assert control.created == 1
    stored = connection_state(graph)

    mutate_claim(
        mutation, workspace_id=graph.workspace_id, season_id=graph.season_id, extras=extras
    )

    with SessionFactory() as session:
        league = session.get(League, graph.league_id)
        assert league is not None
        assert league.installation_id == graph.workspace_id

    denied = Registry(FakeProvider(league_id=graph.provider_league_id))

    with pytest.raises(ProviderCredentialReplacementUnavailableError) as raised:
        replace(graph, denied)

    assert_no_claim_owner_detail(str(raised.value), extras, str(graph.provider_league_id))
    # Denied before provider construction, any provider call, or any write.
    assert denied.created == 0
    assert denied.captured == {}
    assert denied.provider.fetch_calls == []
    assert connection_state(graph) == stored


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_replacement_api_denial_is_a_bounded_conflict(
    make_graph: Callable[[], SyncGraph], extras: Extras, mutation: str
) -> None:
    graph = make_graph()
    registries: list[Registry] = []

    def replacer(*, workspace_id: int, credentials: Any):
        registry = Registry(FakeProvider(league_id=graph.provider_league_id))
        registries.append(registry)

        return replace_provider_credentials(
            SessionFactory,
            workspace_id=workspace_id,
            submitted_credentials=credentials,
            credential_keyring=keyring(),
            registry=registry.registry,
        )

    now = datetime.now(UTC)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=987_654,
        username="claim-backed-admin",
        display_name="Claim Backed Admin",
        workspace_id=graph.workspace_id,
        email=None,
        password_hash="$argon2id$test-placeholder",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_current_workspace_id] = lambda: graph.workspace_id
    app.dependency_overrides[require_session_csrf] = lambda: None
    app.dependency_overrides[get_provider_credential_replacer] = lambda: replacer

    def patch() -> Any:
        return TestClient(app).patch(
            "/api/admin/provider-credentials",
            headers={"Origin": TRUSTED_ORIGIN},
            json={"espn_s2": "synthetic-cookie-value", "swid": "{synthetic-swid-value}"},
        )

    try:
        assert patch().status_code == status.HTTP_200_OK
        stored = connection_state(graph)

        mutate_claim(
            mutation, workspace_id=graph.workspace_id, season_id=graph.season_id, extras=extras
        )
        response = patch()
    finally:
        for dependency in (
            get_current_user,
            get_current_workspace_id,
            require_session_csrf,
            get_provider_credential_replacer,
        ):
            app.dependency_overrides.pop(dependency, None)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert set(response.json()) == {"detail"}
    assert_no_claim_owner_detail(response.text, extras, graph.provider_league_id)
    assert "synthetic-cookie-value" not in response.text
    assert registries[-1].created == 0
    assert connection_state(graph) == stored
