"""Minimal ESPN league-access check for SaaS onboarding.

This module owns every ESPN-specific detail of onboarding validation: the
URL shape, cookie names, status-code semantics and the minimal response
structure. It requests only league settings for one exact League ID and
season, never teams, schedules, players, rosters or draft data, and it
never requires league-manager or team-owner status.

Status semantics follow the pinned espn-api library: a 401 on the season
endpoint is retried once against the league-history endpoint (older
seasons live there), 401 on both means authentication is required, and
404 means ESPN reports no such league. Anything that cannot be classified
safely is reported as an unavailable target, never guessed as private.

Credentials travel only as cookies. They never appear in a URL, log line,
exception message or returned value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import requests

ESPN_FOOTBALL_BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 7.0
# Seasons before this year are served only by the league-history endpoint.
FIRST_SEASON_ENDPOINT_YEAR = 2018
_SETTINGS_VIEW = "mSettings"


class EspnLeagueAccessOutcome(StrEnum):
    """Classified result of one league-access check."""

    ACCESSIBLE = "accessible"
    CREDENTIALS_REQUIRED = "credentials_required"
    AUTHENTICATION_FAILED = "authentication_failed"
    TARGET_UNAVAILABLE = "target_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


@dataclass(frozen=True, slots=True)
class EspnPrivateAccess:
    """Private-league cookies. repr never reveals either value."""

    swid: str
    espn_s2: str

    def __repr__(self) -> str:
        return "EspnPrivateAccess(swid=<redacted>, espn_s2=<redacted>)"

    def cookies(self) -> dict[str, str]:
        return {"SWID": self.swid, "espn_s2": self.espn_s2}


@dataclass(frozen=True, slots=True)
class EspnLeagueMetadata:
    """Provider-verified fields needed to create a canonical League and Season."""

    name: str
    current_week: int
    regular_season_weeks: int


@dataclass(frozen=True, slots=True)
class EspnLeagueAccessResult:
    outcome: EspnLeagueAccessOutcome
    # Present only when accessible and every required field was returned.
    metadata: EspnLeagueMetadata | None = None

    @property
    def accessible(self) -> bool:
        return self.outcome is EspnLeagueAccessOutcome.ACCESSIBLE


class EspnResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


HttpGet = Callable[..., EspnResponse]


def normalize_swid(value: str) -> str:
    """Return the braced SWID form ESPN expects, matching settings handling."""

    swid = value.strip()

    if not swid.startswith("{"):
        swid = "{" + swid

    if not swid.endswith("}"):
        swid = swid + "}"

    return swid


def check_espn_league_access(
    *,
    league_id: str,
    season_year: int,
    private_access: EspnPrivateAccess | None = None,
    http_get: HttpGet | None = None,
) -> EspnLeagueAccessResult:
    """Check whether one exact ESPN league season can be read.

    Makes at most two bounded requests and performs no retries for 4xx
    responses, invalid credentials, invalid targets or malformed bodies.
    """

    get = http_get if http_get is not None else requests.get
    cookies = private_access.cookies() if private_access is not None else None
    expected_id = int(league_id)

    if season_year < FIRST_SEASON_ENDPOINT_YEAR:
        attempts = (_history_request(league_id, season_year),)
    else:
        attempts = (
            _season_request(league_id, season_year),
            _history_request(league_id, season_year),
        )

    for index, (url, params) in enumerate(attempts):
        try:
            response = get(
                url,
                params=params,
                cookies=cookies,
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                allow_redirects=False,
            )
        except requests.RequestException:
            # Timeouts, connection failures and transport errors alike.
            return _result(EspnLeagueAccessOutcome.PROVIDER_UNAVAILABLE)

        status = response.status_code
        is_last_attempt = index == len(attempts) - 1

        if status == 200:
            body = _expected_league_body(response, expected_id, season_year)

            if body is not None:
                return EspnLeagueAccessResult(
                    outcome=EspnLeagueAccessOutcome.ACCESSIBLE,
                    metadata=_league_metadata(body, season_year),
                )

            return _result(EspnLeagueAccessOutcome.TARGET_UNAVAILABLE)

        if status == 401:
            if not is_last_attempt:
                continue

            return _result(
                EspnLeagueAccessOutcome.CREDENTIALS_REQUIRED
                if private_access is None
                else EspnLeagueAccessOutcome.AUTHENTICATION_FAILED
            )

        if status == 429 or status >= 500:
            return _result(EspnLeagueAccessOutcome.PROVIDER_UNAVAILABLE)

        # 404, 403, other 4xx and redirects cannot be classified safely.
        return _result(EspnLeagueAccessOutcome.TARGET_UNAVAILABLE)

    return _result(EspnLeagueAccessOutcome.TARGET_UNAVAILABLE)


def _season_request(league_id: str, season_year: int) -> tuple[str, Mapping[str, object]]:
    return (
        f"{ESPN_FOOTBALL_BASE_URL}/seasons/{season_year}/segments/0/leagues/{league_id}",
        {"view": _SETTINGS_VIEW},
    )


def _history_request(league_id: str, season_year: int) -> tuple[str, Mapping[str, object]]:
    return (
        f"{ESPN_FOOTBALL_BASE_URL}/leagueHistory/{league_id}",
        {"seasonId": season_year, "view": _SETTINGS_VIEW},
    )


def _result(outcome: EspnLeagueAccessOutcome) -> EspnLeagueAccessResult:
    return EspnLeagueAccessResult(outcome=outcome)


def _expected_league_body(
    response: EspnResponse,
    expected_id: int,
    season_year: int,
) -> dict[str, Any] | None:
    """Return the season document when it is the exact league and season with settings."""

    try:
        body = response.json()
    except Exception:
        return None

    # The league-history endpoint returns a list of season documents.
    if isinstance(body, list):
        matches = [
            item for item in body if isinstance(item, dict) and item.get("seasonId") == season_year
        ]

        if len(matches) != 1:
            return None

        body = matches[0]

    if not isinstance(body, dict):
        return None

    league_id = body.get("id")
    returned_season = body.get("seasonId")
    settings = body.get("settings")

    if (
        _is_int(league_id)
        and league_id == expected_id
        and returned_season == season_year
        and isinstance(settings, dict)
        and isinstance(settings.get("name"), str)
    ):
        return body

    return None


def _league_metadata(body: dict[str, Any], season_year: int) -> EspnLeagueMetadata | None:
    """Read name, current week and regular-season length as the espn-api library does.

    Returns None rather than guessing when any field is missing or malformed.
    """

    settings = body["settings"]
    name = settings["name"].strip()
    schedule = settings.get("scheduleSettings")
    status = body.get("status")
    scoring_period = body.get("scoringPeriodId")
    regular_season_weeks = (
        schedule.get("matchupPeriodCount") if isinstance(schedule, dict) else None
    )

    if (
        not name
        or len(name) > 255
        or not _is_int(scoring_period)
        or scoring_period < 0
        or not _is_int(regular_season_weeks)
        or regular_season_weeks < 1
    ):
        return None

    current_week = scoring_period

    if season_year >= FIRST_SEASON_ENDPOINT_YEAR:
        final_period = status.get("finalScoringPeriod") if isinstance(status, dict) else None

        if not _is_int(final_period):
            return None

        current_week = min(scoring_period, final_period)

    return EspnLeagueMetadata(
        name=name,
        current_week=current_week,
        regular_season_weeks=regular_season_weeks,
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
