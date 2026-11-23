"""ESPN league-access classification. No test makes a network request."""

from collections.abc import Iterator
from typing import Any

import pytest
import requests

from app.providers.espn.league_access import (
    CONNECT_TIMEOUT_SECONDS,
    READ_TIMEOUT_SECONDS,
    EspnLeagueAccessOutcome,
    EspnPrivateAccess,
    check_espn_league_access,
    normalize_swid,
)

LEAGUE_ID = "424242"
SEASON = 2024
SYNTHETIC = EspnPrivateAccess(
    swid="{SYNTHETIC-SWID-0000-TEST}",
    espn_s2="synthetic-espn-s2-not-a-real-cookie",
)


class FakeResponse:
    def __init__(self, status_code: int, body: Any = None, *, invalid_json: bool = False):
        self.status_code = status_code
        self._body = body
        self._invalid_json = invalid_json

    def json(self) -> Any:
        if self._invalid_json:
            raise ValueError("not json")

        return self._body


def league_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": int(LEAGUE_ID),
        "seasonId": SEASON,
        "settings": {"name": "Synthetic League"},
        # Ignored: membership is never an authorization requirement.
        "members": [{"id": "member", "isLeagueManager": False}],
    }
    body.update(overrides)

    return body


class FakeGet:
    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Live network requests are forbidden in tests.")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)
    yield


def check(fake: FakeGet, **kwargs: Any) -> EspnLeagueAccessOutcome:
    return check_espn_league_access(
        league_id=LEAGUE_ID, season_year=SEASON, http_get=fake, **kwargs
    ).outcome


def test_public_league_is_accessible_with_one_bounded_anonymous_request() -> None:
    fake = FakeGet(FakeResponse(200, league_body()))

    assert check(fake) is EspnLeagueAccessOutcome.ACCESSIBLE
    assert len(fake.calls) == 1

    call = fake.calls[0]
    assert call["url"].endswith(f"/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}")
    assert call["params"] == {"view": "mSettings"}
    assert call["cookies"] is None
    assert call["timeout"] == (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS) == (3.0, 7.0)
    assert call["allow_redirects"] is False


def test_history_fallback_after_401_finds_public_past_season() -> None:
    fake = FakeGet(FakeResponse(401), FakeResponse(200, [league_body()]))

    assert check(fake) is EspnLeagueAccessOutcome.ACCESSIBLE
    assert fake.calls[1]["url"].endswith(f"/leagueHistory/{LEAGUE_ID}")
    assert fake.calls[1]["params"] == {"seasonId": SEASON, "view": "mSettings"}


def test_401_on_both_endpoints_without_cookies_requires_credentials() -> None:
    fake = FakeGet(FakeResponse(401), FakeResponse(401))

    assert check(fake) is EspnLeagueAccessOutcome.CREDENTIALS_REQUIRED
    assert len(fake.calls) == 2


def test_private_access_succeeds_without_manager_or_owner_status() -> None:
    fake = FakeGet(FakeResponse(200, league_body()))

    assert check(fake, private_access=SYNTHETIC) is EspnLeagueAccessOutcome.ACCESSIBLE
    assert fake.calls[0]["cookies"] == {
        "SWID": SYNTHETIC.swid,
        "espn_s2": SYNTHETIC.espn_s2,
    }


def test_401_with_cookies_is_authentication_failure() -> None:
    fake = FakeGet(FakeResponse(401), FakeResponse(401))

    assert check(fake, private_access=SYNTHETIC) is EspnLeagueAccessOutcome.AUTHENTICATION_FAILED


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(404),
        FakeResponse(403),
        FakeResponse(400),
        FakeResponse(302),
        FakeResponse(200, invalid_json=True),
        FakeResponse(200, ["unexpected"]),
        FakeResponse(200, league_body(id=999)),
        FakeResponse(200, league_body(id="424242")),
        FakeResponse(200, league_body(seasonId=2023)),
        FakeResponse(200, league_body(settings=None)),
        FakeResponse(200, league_body(settings={"name": 7})),
    ],
)
def test_unclassifiable_responses_are_target_unavailable(response: FakeResponse) -> None:
    fake = FakeGet(response)

    assert check(fake) is EspnLeagueAccessOutcome.TARGET_UNAVAILABLE
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(500),
        FakeResponse(503),
        FakeResponse(429),
        requests.Timeout("timed out"),
        requests.ConnectionError("refused"),
    ],
)
def test_transient_failures_are_provider_unavailable_without_retry(
    response: FakeResponse | Exception,
) -> None:
    fake = FakeGet(response)

    assert check(fake) is EspnLeagueAccessOutcome.PROVIDER_UNAVAILABLE
    assert len(fake.calls) == 1


def test_pre_2018_season_uses_history_endpoint_only() -> None:
    fake = FakeGet(FakeResponse(401))

    outcome = check_espn_league_access(league_id=LEAGUE_ID, season_year=2015, http_get=fake)

    assert outcome.outcome is EspnLeagueAccessOutcome.CREDENTIALS_REQUIRED
    assert len(fake.calls) == 1
    assert "/leagueHistory/" in fake.calls[0]["url"]


def test_credentials_never_appear_in_urls_params_or_repr() -> None:
    fake = FakeGet(FakeResponse(401), FakeResponse(401))

    check(fake, private_access=SYNTHETIC)

    for call in fake.calls:
        visible = f"{call['url']} {call['params']}"
        assert SYNTHETIC.swid not in visible
        assert SYNTHETIC.espn_s2 not in visible

    assert SYNTHETIC.swid not in repr(SYNTHETIC)
    assert SYNTHETIC.espn_s2 not in repr(SYNTHETIC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ABC-123", "{ABC-123}"),
        ("{ABC-123}", "{ABC-123}"),
        ("  {ABC-123  ", "{ABC-123}"),
    ],
)
def test_swid_normalization(raw: str, expected: str) -> None:
    assert normalize_swid(raw) == expected
