"""ESPN League metadata extraction (pure, fake transport, synthetic bodies)."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from app.providers.espn.league_access import (
    EspnLeagueAccessOutcome,
    EspnLeagueMetadata,
    check_espn_league_access,
)

LEAGUE = 123456789


class Response:
    def __init__(self, body: Any) -> None:
        self.status_code = 200
        self._body = body

    def json(self) -> Any:
        return self._body


def body(season: int = 2024, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": LEAGUE,
        "seasonId": season,
        "scoringPeriodId": 5,
        "status": {"finalScoringPeriod": 17},
        "settings": {
            "name": "  Synthetic League  ",
            "scheduleSettings": {"matchupPeriodCount": 14},
        },
    }
    value.update(overrides)
    return value


def check(document: Any, season: int = 2024):
    def http_get(url: str, **kwargs: Any) -> Response:
        return Response([document] if "/leagueHistory/" in url else document)

    return check_espn_league_access(league_id=str(LEAGUE), season_year=season, http_get=http_get)


def test_full_body_yields_stripped_metadata() -> None:
    result = check(body())

    assert result.outcome is EspnLeagueAccessOutcome.ACCESSIBLE
    assert result.metadata == EspnLeagueMetadata(
        name="Synthetic League", current_week=5, regular_season_weeks=14
    )


def test_current_week_is_capped_by_final_scoring_period() -> None:
    result = check(body(scoringPeriodId=18))

    assert result.metadata is not None
    assert result.metadata.current_week == 17


def test_history_season_uses_list_body_without_status() -> None:
    document = body(season=2015, scoringPeriodId=18)
    del document["status"]

    result = check(document, season=2015)

    assert result.outcome is EspnLeagueAccessOutcome.ACCESSIBLE
    assert result.metadata == EspnLeagueMetadata(
        name="Synthetic League", current_week=18, regular_season_weeks=14
    )


def _mutated(mutation: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
    document = deepcopy(body())
    mutation(document)
    return document


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["settings"].pop("scheduleSettings"),
        lambda d: d["settings"]["scheduleSettings"].pop("matchupPeriodCount"),
        lambda d: d["settings"]["scheduleSettings"].update(matchupPeriodCount=0),
        lambda d: d["settings"]["scheduleSettings"].update(matchupPeriodCount=True),
        lambda d: d.update(scoringPeriodId=True),
        lambda d: d.update(scoringPeriodId=-1),
        lambda d: d.pop("scoringPeriodId"),
        lambda d: d["settings"].update(name="   "),
        lambda d: d["settings"].update(name="x" * 256),
        lambda d: d["status"].pop("finalScoringPeriod"),
        lambda d: d["status"].update(finalScoringPeriod=True),
        lambda d: d.pop("status"),
    ],
    ids=[
        "no-schedule",
        "no-matchup-count",
        "zero-matchup-count",
        "bool-matchup-count",
        "bool-scoring-period",
        "negative-scoring-period",
        "missing-scoring-period",
        "blank-name",
        "long-name",
        "no-final-period",
        "bool-final-period",
        "no-status",
    ],
)
def test_incomplete_metadata_is_accessible_without_metadata(
    mutation: Callable[[dict[str, Any]], object],
) -> None:
    result = check(_mutated(mutation))

    assert result.outcome is EspnLeagueAccessOutcome.ACCESSIBLE
    assert result.metadata is None


def test_name_at_limit_is_accepted() -> None:
    result = check(_mutated(lambda d: d["settings"].update(name="x" * 255)))

    assert result.metadata is not None
    assert len(result.metadata.name) == 255
