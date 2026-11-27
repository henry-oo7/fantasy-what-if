"""Routine tests for the personal League import endpoint (synthetic data only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.settings import get_settings
from app.db.auth_models import RegistrationRateLimit
from app.db.league_access_models import UserPreference
from app.db.models import Season
from app.db.platform_enums import (
    BackgroundJobType,
    FantasyProvider,
    SeasonSyncState,
    UserSeasonProofMode,
)
from app.db.platform_models import BackgroundJob
from app.db.session import SessionFactory
from app.main import app
from app.services.league_import import VerifiedLeagueImport
from app.services.provider_onboarding import ProviderOnboardingError
from tests.support.league_import import (  # noqa: F401
    LEAGUE_NAME,
    SEASON,
    SECRETS,
    WRONG_S2,
    api_espn,
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
)

RESPONSE_KEYS = {
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
ERROR_KEYS = {"detail", "code", "retryable"}
CURRENT_YEAR = datetime.now(UTC).year
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def rate_limit_ids() -> set[int]:
    with SessionFactory() as session:
        return set(session.scalars(select(RegistrationRateLimit.id)))


def assert_no_secrets(text: str) -> None:
    assert "SECRET-PROVIDER-DETAIL" not in text
    assert not any(secret in text for secret in SECRETS)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": 1},
        {"workspace_id": 1},
        {"provider_connection_id": 1},
        {"season_id": 1},
        {"proof_mode": "public"},
        {"status": "active"},
        {"job_id": 1},
    ],
    ids=lambda value: next(iter(value)),
)
def test_unknown_fields_are_rejected(api_espn, registry, league_id, extra) -> None:  # noqa: F811
    caller = create_caller(registry)
    before = rate_limit_ids()

    response = post_import(caller, import_body(league_id, **extra))

    assert response.status_code == 422
    assert api_espn.calls == []
    assert rate_limit_ids() == before
    assert import_state(league_id).leagues == 0


def _bad_bodies(league: str) -> dict[str, dict[str, Any]]:
    good = import_body(league)
    return {
        "non-digit-id": {**good, "provider_league_id": "12ab34"},
        "id-over-64": {**good, "provider_league_id": "1" * 65},
        "id-over-15-digits": {**good, "provider_league_id": "1" * 16},
        "future-year": {**good, "season_year": CURRENT_YEAR + 1},
        "year-1999": {**good, "season_year": 1999},
        "only-swid": {**good, "credentials": {"swid": "{SYNTHETIC-A}"}},
        "only-s2": {**good, "credentials": {"espn_s2": "synthetic-s2-value"}},
        "blank-swid": {**good, "credentials": {"swid": "  ", "espn_s2": "synthetic-s2-value"}},
        "blank-s2": {**good, "credentials": {"swid": "{SYNTHETIC-A}", "espn_s2": "  "}},
        "extra-in-credentials": {**good, "credentials": {**credentials_body(), "extra": "x"}},
    }


@pytest.mark.parametrize("case", list(_bad_bodies("1")))
def test_invalid_requests_are_rejected_without_provider_call(
    api_espn,  # noqa: F811
    registry,  # noqa: F811
    league_id,  # noqa: F811
    case,
) -> None:
    caller = create_caller(registry)
    before = rate_limit_ids()

    response = post_import(caller, _bad_bodies(league_id)[case])

    assert response.status_code == 422
    assert api_espn.calls == []
    assert rate_limit_ids() == before
    assert_no_secrets(response.text)


def test_leading_zeros_are_normalized(api_espn, registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry)

    response = post_import(caller, import_body("000" + league_id))

    assert response.status_code == 200
    assert response.json()["provider_league_id"] == league_id


# --------------------------------------------------------------------------
# Unsupported providers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "target"),
    [
        ("sleeper", None),
        ("yahoo", None),
        ("yahoo", "449.l.12345"),
        ("unknown", None),
    ],
)
def test_unsupported_provider(api_espn, registry, league_id, provider, target) -> None:  # noqa: F811
    caller = create_caller(registry)
    before = rate_limit_ids()
    body = {**import_body(league_id), "provider": provider}
    body["provider_league_id"] = target or league_id

    response = post_import(caller, body)

    assert response.status_code == 422
    payload = response.json()
    assert set(payload) == ERROR_KEYS
    assert payload["code"] == "unsupported_provider"
    assert payload["retryable"] is False
    assert api_espn.calls == []
    assert rate_limit_ids() == before
    state = import_state(league_id, user_ids=(caller.user_id,))
    assert (state.leagues, state.seasons, state.access, state.connections, state.active_jobs) == (
        0,
        0,
        0,
        0,
        0,
    )


# --------------------------------------------------------------------------
# Response serialization
# --------------------------------------------------------------------------


def test_first_public_import_response(api_espn, registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry)

    response = post_import(caller, import_body(league_id))

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == RESPONSE_KEYS
    assert payload["provider"] == "espn"
    assert payload["provider_league_id"] == league_id
    assert payload["league_name"] == LEAGUE_NAME
    assert payload["season_year"] == SEASON
    assert payload["access_status"] == "active"
    assert payload["sync_state"] == "queued"
    assert payload["data_available"] is False
    assert payload["sync_queued"] is True
    assert import_state(league_id).active_jobs == 1


def test_private_import_response_has_no_identifiers_or_secrets(
    api_espn,  # noqa: F811
    registry,  # noqa: F811
    league_id,  # noqa: F811
) -> None:
    api_espn.mode = "private"
    caller = create_caller(registry)

    response = post_import(caller, import_body(league_id, credentials=credentials_body()))

    assert response.status_code == 200
    assert set(response.json()) == RESPONSE_KEYS
    assert_no_secrets(response.text)
    for forbidden in ("user_id", "workspace_id", "connection", "credentials"):
        assert forbidden not in response.text
    assert import_state(league_id, user_ids=(caller.user_id,)).connections == 1


# --------------------------------------------------------------------------
# Error mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "credentials", "status", "code", "retryable"),
    [
        ("missing", None, 422, "provider_validation_failed", False),
        ("error500", None, 503, "provider_unavailable", True),
        ("timeout", None, 503, "provider_unavailable", True),
        ("private", None, 422, "provider_credentials_required", False),
        ("private", WRONG_S2, 422, "provider_authentication_failed", False),
        ("no_metadata", None, 422, "provider_validation_failed", False),
        ("wrong_league", None, 422, "provider_validation_failed", False),
    ],
)
def test_stable_error_mapping(
    api_espn,  # noqa: F811
    registry,  # noqa: F811
    league_id,  # noqa: F811
    mode,
    credentials,
    status,
    code,
    retryable,
) -> None:
    api_espn.mode = mode
    caller = create_caller(registry)
    body = import_body(league_id)

    if credentials is not None:
        body["credentials"] = credentials_body(credentials)

    response = post_import(caller, body)

    assert response.status_code == status
    payload = response.json()
    assert set(payload) == ERROR_KEYS
    assert payload["code"] == code
    assert payload["retryable"] is retryable
    assert_no_secrets(response.text)
    assert import_state(league_id, user_ids=(caller.user_id,)).access == 0


# --------------------------------------------------------------------------
# Throttle
# --------------------------------------------------------------------------


def test_throttle_counts_failed_proofs_and_blocks_third(registry, league_id, espn) -> None:  # noqa: F811
    caller = create_caller(registry)
    espn.mode = "missing"

    for _ in range(2):
        with pytest.raises(ProviderOnboardingError) as failure:
            run_import(caller, league_id, espn, user_maximum=2)
        assert failure.value.code == "provider_validation_failed"

    calls = len(espn.calls)

    with pytest.raises(ProviderOnboardingError) as blocked:
        run_import(caller, league_id, espn, user_maximum=2)

    assert blocked.value.code == "league_import_throttled"
    assert blocked.value.status_code == 429
    assert blocked.value.retry_after_seconds >= 1
    assert len(espn.calls) == calls


def test_throttle_is_per_user(registry, league_id, espn) -> None:  # noqa: F811
    first = create_caller(registry)
    second = create_caller(registry)

    run_import(first, league_id, espn, user_maximum=1)

    with pytest.raises(ProviderOnboardingError):
        run_import(first, league_id, espn, user_maximum=1)

    assert run_import(second, league_id, espn, user_maximum=1).provider_league_id == league_id


def test_throttle_over_api_sets_retry_after(api_espn, registry, league_id) -> None:  # noqa: F811
    base = app.dependency_overrides[get_settings]()
    app.dependency_overrides[get_settings] = lambda: base.model_copy(
        update={"provider_validation_workspace_maximum_requests": 1}
    )
    caller = create_caller(registry)

    first = post_import(caller, import_body(league_id))
    second = post_import(caller, import_body(league_id))

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert set(second.json()) == ERROR_KEYS
    assert second.json()["code"] == "league_import_throttled"


def test_throttle_rows_do_not_contain_identifiers(registry, league_id, espn) -> None:  # noqa: F811
    espn.mode = "private"
    caller = create_caller(registry)
    before = rate_limit_ids()

    run_import(caller, league_id, espn, private_access=private())

    with SessionFactory() as session:
        rows = [
            row for row in session.scalars(select(RegistrationRateLimit)) if row.id not in before
        ]

    assert {row.action for row in rows} == {"league_import_user", "league_import_source"}
    forbidden = [
        league_id,
        str(caller.user_id),
        f"user:{caller.user_id}",
        "example.com",
        *SECRETS,
    ]
    for row in rows:
        assert not any(value in row.identity_hash for value in forbidden)


# --------------------------------------------------------------------------
# Preferences
# --------------------------------------------------------------------------


def _set_preference(user_id: int, season_id: int | None) -> None:
    with SessionFactory() as session:
        session.execute(delete(UserPreference).where(UserPreference.user_id == user_id))
        session.add(UserPreference(user_id=user_id, default_league_season_id=season_id))
        session.commit()


def test_existing_default_is_not_replaced(registry, league_id, espn) -> None:  # noqa: F811
    other = unique_league_id()
    registry.league_ids.append(other)
    owner = create_caller(registry)
    caller = create_caller(registry)
    earlier = run_import(owner, other, espn)
    _set_preference(caller.user_id, earlier.season_id)

    run_import(caller, league_id, espn)

    assert preference(caller.user_id) == earlier.season_id


def test_null_default_is_filled(registry, league_id, espn) -> None:  # noqa: F811
    caller = create_caller(registry)
    _set_preference(caller.user_id, None)

    result = run_import(caller, league_id, espn)

    assert preference(caller.user_id) == result.season_id


# --------------------------------------------------------------------------
# Sync-state mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {
                "sync_state": SeasonSyncState.FRESH,
                "last_successful_sync_at": NOW,
                "data_version": 1,
            },
            (False, True, SeasonSyncState.FRESH, 0),
        ),
        (
            {"sync_state": SeasonSyncState.STALE, "data_version": 1},
            (True, True, SeasonSyncState.QUEUED, 1),
        ),
        (
            {"sync_state": SeasonSyncState.FAILED, "data_version": 0},
            (True, False, SeasonSyncState.QUEUED, 1),
        ),
        (
            {"sync_state": None, "last_synced_at": NOW},
            (True, True, SeasonSyncState.QUEUED, 1),
        ),
        (
            {"sync_state": None},
            (True, False, SeasonSyncState.QUEUED, 1),
        ),
    ],
    ids=["fresh", "stale", "failed-no-data", "legacy-with-data", "legacy-no-data"],
)
def test_sync_state_mapping(registry, league_id, espn, fields, expected) -> None:  # noqa: F811
    first = create_caller(registry)
    second = create_caller(registry)
    season_id = run_import(first, league_id, espn).season_id

    with SessionFactory() as session:
        season = session.get(Season, season_id)
        assert season is not None
        season.last_synced_at = None
        season.last_successful_sync_at = None
        season.data_version = 0

        for name, value in fields.items():
            setattr(season, name, value)

        session.execute(
            delete(BackgroundJob).where(
                BackgroundJob.job_type == BackgroundJobType.LEAGUE_SEASON_SYNC,
                BackgroundJob.idempotency_key == f"league-season-sync:{season_id}",
            )
        )
        session.commit()

    result = run_import(second, league_id, espn)

    assert (
        result.sync_queued,
        result.data_available,
        result.sync_state,
        import_state(league_id).active_jobs,
    ) == expected


# --------------------------------------------------------------------------
# Safe repr
# --------------------------------------------------------------------------


def test_reprs_hide_credentials() -> None:
    verified = VerifiedLeagueImport(
        provider=FantasyProvider.ESPN,
        provider_league_id="123456",
        season_year=SEASON,
        league_name=LEAGUE_NAME,
        current_week=1,
        regular_season_weeks=14,
        proof_mode=UserSeasonProofMode.PRIVATE_CREDENTIALS,
        private_access=private(),
    )

    for text in (repr(verified), repr(private())):
        assert not any(secret in text for secret in SECRETS)
