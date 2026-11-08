"""Structural protocols for objects returned by espn-api."""

from __future__ import annotations

from typing import Any, Protocol


class EspnLeagueSettings(Protocol):
    name: str
    reg_season_count: int


class EspnTeam(Protocol):
    team_id: int
    team_name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float


class EspnPlayer(Protocol):
    playerId: int
    name: str
    proTeam: str | None
    position: str
    lineupSlot: str
    eligibleSlots: list[Any]
    points: float
    projected_points: float | None


class EspnMatchup(Protocol):
    home_team: EspnTeam
    away_team: EspnTeam
    home_score: float
    away_score: float
    home_lineup: list[EspnPlayer]
    away_lineup: list[EspnPlayer]


class EspnLeague(Protocol):
    league_id: int
    year: int
    current_week: int
    settings: EspnLeagueSettings
    teams: list[EspnTeam]

    def box_scores(
        self,
        week: int,
    ) -> list[EspnMatchup]:
        """Return ESPN box scores for one scoring week."""
        ...
