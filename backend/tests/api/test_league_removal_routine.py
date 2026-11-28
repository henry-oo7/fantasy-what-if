"""Routine API tests for ``POST /api/leagues/{provider}/{provider_league_id}/removal``.

Synthetic data only; the fake ESPN transport is the sole provider boundary.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth.cookies import csrf_cookie_name
from app.core.settings import get_settings
from app.db.auth_models import AuthSessionPurpose, RegistrationRateLimit
from app.db.session import SessionFactory
from app.main import app
from tests.support.league_import import (  # noqa: F401
    LEAGUE_NAME,
    OTHER_S2,
    SECRETS,
    TRUSTED_ORIGIN,
    Caller,
    FakeEspn,
    create_caller,
    forbid_live_network,
    import_state,
    league_id,
    private,
    registry,
    run_import,
    unique_league_id,
    user_connections,
)

CONFIRMATION = LEAGUE_NAME
SUCCESS = {
    "detail": "The League was removed from your account.",
    "code": "league_access_removed",
}
UNAVAILABLE = {
    "detail": "League removal is unavailable.",
    "code": "league_removal_unavailable",
    "retryable": False,
}
MISMATCH = {
    "detail": "Type the League name exactly as shown.",
    "code": "league_removal_confirmation_mismatch",
    "retryable": True,
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _client(caller: Caller | None, *, cookies: bool = True) -> TestClient:
    client = TestClient(app)

    if caller is not None and cookies:
        settings = get_settings()
        client.cookies.set(settings.session_cookie_name, caller.session_token)
        client.cookies.set(csrf_cookie_name(settings), caller.csrf_token)

    return client


def path(league: str, provider: str = "espn") -> str:
    return f"/api/leagues/{provider}/{league}/removal"


def post_removal(
    caller: Caller | None,
    league: str,
    body: dict[str, Any] | None = None,
    *,
    provider: str = "espn",
    origin: str | None = TRUSTED_ORIGIN,
    csrf: bool = True,
    cookies: bool = True,
):
    with _client(caller, cookies=cookies) as client:
        headers: dict[str, str] = {}

        if origin is not None:
            headers["Origin"] = origin

        if caller is not None and csrf:
            headers["X-CSRF-Token"] = caller.csrf_token

        return client.post(
            path(league, provider),
            headers=headers,
            json={"confirmation": CONFIRMATION} if body is None else body,
        )


def get_json(caller: Caller, url: str):
    with _client(caller) as client:
        return client.get(url)


def listed_leagues(caller: Caller) -> list[str]:
    response = get_json(caller, "/api/leagues")

    assert response.status_code == 200
    return [item["provider_league_id"] for item in response.json()["leagues"]]


def seed(caller: Caller, league: str) -> None:
    run_import(caller, league, FakeEspn())


def seed_private(caller: Caller, league: str, s2: str) -> None:
    run_import(caller, league, FakeEspn(mode="private"), private_access=private(s2))


def assert_no_leak(text: str, *callers: Caller) -> None:
    for secret in SECRETS:
        assert secret not in text

    lowered = text.lower()
    for word in ("ciphertext", "swid", "espn_s2", "workspace", "claim", "example.com"):
        assert word not in lowered

    for caller in callers:
        assert f'"{caller.user_id}"' not in text


def rate_limit_rows() -> int:
    with SessionFactory() as session:
        return int(session.scalar(select(func.count()).select_from(RegistrationRateLimit)) or 0)


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


def test_success_removes_only_callers_access_and_connection(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    other = create_caller(registry, personal=True)
    seed_private(caller, league_id, "synthetic-import-espn-s2-not-real-0001")
    seed_private(other, league_id, OTHER_S2)
    both = (caller.user_id, other.user_id)
    before = import_state(league_id, user_ids=both)
    assert before.access >= 2
    assert user_connections(caller.user_id)

    response = post_removal(caller, league_id)

    assert response.status_code == 200
    assert response.json() == SUCCESS
    assert set(response.json()) == {"detail", "code"}
    assert_no_leak(response.text, caller, other)

    after = import_state(league_id, user_ids=both)
    assert after.leagues == before.leagues == 1
    assert after.seasons == before.seasons
    assert 0 < after.access < before.access
    assert not [c for c in user_connections(caller.user_id) if c.provider_league_id == league_id]
    assert [c for c in user_connections(other.user_id) if c.provider_league_id == league_id]

    # Other user still lists the League; the caller no longer does.
    assert league_id in listed_leagues(other)
    assert league_id not in listed_leagues(caller)


def test_success_keeps_the_session_and_other_leagues(registry, league_id) -> None:  # noqa: F811
    second = unique_league_id()
    registry.league_ids.append(second)
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)
    seed(caller, second)
    assert {league_id, second} <= set(listed_leagues(caller))

    response = post_removal(caller, league_id)

    assert response.status_code == 200
    listed = listed_leagues(caller)
    assert league_id not in listed
    assert second in listed
    assert get_json(caller, "/api/auth/me").status_code == 200
    assert import_state(second, user_ids=(caller.user_id,)).access >= 1


@pytest.mark.parametrize(
    "confirmation",
    [CONFIRMATION.upper(), CONFIRMATION.lower(), f"  {CONFIRMATION.replace(' ', '   ')}\t "],
)
def test_confirmation_is_case_and_whitespace_tolerant(registry, league_id, confirmation) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)

    response = post_removal(caller, league_id, {"confirmation": confirmation})

    assert response.status_code == 200
    assert response.json() == SUCCESS
    assert league_id not in listed_leagues(caller)


def test_removed_league_can_be_imported_again(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)
    assert post_removal(caller, league_id).status_code == 200
    assert league_id not in listed_leagues(caller)

    seed(caller, league_id)

    assert league_id in listed_leagues(caller)
    assert import_state(league_id, user_ids=(caller.user_id,)).leagues == 1
    assert post_removal(caller, league_id).status_code == 200


def test_second_removal_is_unavailable(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)
    assert post_removal(caller, league_id).status_code == 200

    response = post_removal(caller, league_id)

    assert response.status_code == 404
    assert response.json() == UNAVAILABLE


# --------------------------------------------------------------------------
# Wrong confirmation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("confirmation", ["Wrong League Name", "Synthetic Import", "x"])
def test_wrong_confirmation_changes_nothing(registry, league_id, confirmation) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    other = create_caller(registry, personal=True)
    seed_private(caller, league_id, "synthetic-import-espn-s2-not-real-0001")
    seed(other, league_id)
    both = (caller.user_id, other.user_id)
    before = import_state(league_id, user_ids=both)
    connections = len(user_connections(caller.user_id))

    response = post_removal(caller, league_id, {"confirmation": confirmation})

    assert response.status_code == 400
    assert response.json() == MISMATCH
    assert_no_leak(response.text, caller, other)
    assert import_state(league_id, user_ids=both) == before
    assert len(user_connections(caller.user_id)) == connections
    assert league_id in listed_leagues(caller)
    assert get_json(caller, "/api/auth/me").status_code == 200


# --------------------------------------------------------------------------
# Indistinguishable unavailable outcomes
# --------------------------------------------------------------------------


def test_no_access_and_unknown_league_are_byte_identical(registry, league_id) -> None:  # noqa: F811
    owner = create_caller(registry, personal=True)
    stranger = create_caller(registry, personal=True)
    seed_private(owner, league_id, "synthetic-import-espn-s2-not-real-0001")
    before = import_state(league_id, user_ids=(owner.user_id, stranger.user_id))

    no_access = post_removal(stranger, league_id)
    unknown = post_removal(stranger, unique_league_id())
    wrong_phrase = post_removal(stranger, league_id, {"confirmation": "Wrong League Name"})

    for response in (no_access, unknown, wrong_phrase):
        assert response.status_code == 404
        assert response.json() == UNAVAILABLE
        assert_no_leak(response.text, owner, stranger)

    assert no_access.content == unknown.content == wrong_phrase.content
    assert import_state(league_id, user_ids=(owner.user_id, stranger.user_id)) == before
    assert league_id in listed_leagues(owner)


@pytest.mark.parametrize("provider", ["yahoo", "sleeper", "bogus", "ESPN"])
def test_unsupported_provider_is_unavailable(registry, league_id, provider) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)
    known = post_removal(create_caller(registry, personal=True), league_id)

    response = post_removal(caller, league_id, provider=provider)

    assert response.status_code == 404
    assert response.json() == UNAVAILABLE
    assert response.content == known.content
    assert league_id in listed_leagues(caller)


@pytest.mark.parametrize("bad_id", ["abc", "12ab34", "-5", "0000000000x"])
def test_invalid_league_id_is_unavailable(registry, bad_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    response = post_removal(caller, bad_id)

    assert response.status_code == 404
    assert response.json() == UNAVAILABLE


def test_unverified_personal_caller_is_unavailable(registry, league_id) -> None:  # noqa: F811
    verified = create_caller(registry, personal=True)
    seed(verified, league_id)
    unverified = create_caller(registry, personal=True, verified=False)

    response = post_removal(unverified, league_id)

    assert response.status_code == 404
    assert response.json() == UNAVAILABLE
    assert league_id in listed_leagues(verified)


def test_legacy_workspace_administrator_is_unavailable(registry, league_id) -> None:  # noqa: F811
    verified = create_caller(registry, personal=True)
    seed(verified, league_id)
    legacy = create_caller(registry)

    response = post_removal(legacy, league_id)

    assert response.status_code == 404
    assert response.json() == UNAVAILABLE
    assert_no_leak(response.text, legacy)
    assert league_id in listed_leagues(verified)


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"confirmation": ""},
        {"confirmation": "x" * 201},
        {"confirmation": None},
        {"confirmation": 5},
    ],
    ids=["empty", "blank", "over-200", "null", "not-string"],
)
def test_invalid_bodies_are_rejected(registry, league_id, body) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)

    response = post_removal(caller, league_id, body)

    assert response.status_code == 422
    assert league_id in listed_leagues(caller)


def test_boundary_length_confirmation_passes_validation(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)

    response = post_removal(caller, league_id, {"confirmation": "y" * 200})

    assert response.status_code == 400
    assert response.json() == MISMATCH


# --------------------------------------------------------------------------
# Authentication, CSRF, origin, method
# --------------------------------------------------------------------------


def test_unauthenticated_request_is_unauthorized(league_id) -> None:  # noqa: F811
    assert post_removal(None, league_id).status_code == 401


def test_missing_session_cookie_is_unauthorized(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)

    assert post_removal(caller, league_id, cookies=False).status_code == 401
    assert league_id in listed_leagues(caller)


def test_missing_csrf_header_is_forbidden(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)

    assert post_removal(caller, league_id, csrf=False).status_code == 403
    assert league_id in listed_leagues(caller)


def test_incorrect_csrf_header_is_forbidden(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)

    with _client(caller) as client:
        response = client.post(
            path(league_id),
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": "synthetic-wrong-csrf"},
            json={"confirmation": CONFIRMATION},
        )

    assert response.status_code == 403
    assert league_id in listed_leagues(caller)


def test_untrusted_origin_is_forbidden(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)

    response = post_removal(caller, league_id, origin="https://evil.example.invalid")

    assert response.status_code == 403
    assert league_id in listed_leagues(caller)


def test_deletion_recovery_session_is_refused(registry, league_id) -> None:  # noqa: F811
    owner = create_caller(registry, personal=True)
    seed(owner, league_id)
    caller = create_caller(registry, personal=True, purpose=AuthSessionPurpose.DELETION_RECOVERY)

    response = post_removal(caller, league_id)

    assert response.status_code == 403
    assert response.json()["code"] == "session_restricted"
    assert league_id in listed_leagues(owner)


def test_get_is_not_allowed(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    assert get_json(caller, path(league_id)).status_code == 405


# --------------------------------------------------------------------------
# Throttle
# --------------------------------------------------------------------------


def test_twenty_first_attempt_is_throttled(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    seed(caller, league_id)

    for _ in range(20):
        response = post_removal(caller, league_id, {"confirmation": "Wrong League Name"})
        assert response.status_code == 400

    throttled = post_removal(caller, league_id, {"confirmation": "Wrong League Name"})

    assert throttled.status_code == 429
    assert throttled.json() == {
        "detail": "Too many attempts. Try again later.",
        "code": "league_removal_throttled",
        "retryable": True,
    }
    assert int(throttled.headers["Retry-After"]) > 0
    assert_no_leak(throttled.text, caller)

    # The throttle precedes every other check: the correct confirmation is refused too.
    assert post_removal(caller, league_id).status_code == 429
    assert league_id in listed_leagues(caller)


def test_attempts_are_counted_before_any_check(registry) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    missing = unique_league_id()

    for _ in range(20):
        assert post_removal(caller, missing).status_code == 404

    assert post_removal(caller, missing).status_code == 429


def test_unsupported_provider_attempts_are_counted(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)

    for _ in range(20):
        assert post_removal(caller, league_id, provider="yahoo").status_code == 404

    assert post_removal(caller, league_id, provider="yahoo").status_code == 429


def test_throttle_is_per_user(registry, league_id) -> None:  # noqa: F811
    busy = create_caller(registry, personal=True)
    fresh = create_caller(registry, personal=True)
    seed(fresh, league_id)

    for _ in range(21):
        post_removal(busy, league_id, {"confirmation": "Wrong League Name"})

    assert post_removal(busy, league_id).status_code == 429
    assert post_removal(fresh, league_id).status_code == 200


def test_attempts_are_persisted(registry, league_id) -> None:  # noqa: F811
    caller = create_caller(registry, personal=True)
    before = rate_limit_rows()

    assert post_removal(caller, league_id).status_code == 404

    assert rate_limit_rows() > before
