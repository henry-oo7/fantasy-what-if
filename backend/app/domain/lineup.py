from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlayerWeekSnapshot:
    player_id: int
    player_name: str
    pro_team: str | None
    default_position: str
    actual_lineup_slot: str
    eligible_lineup_slots: tuple[str, ...]
    points: float
    projected_points: float | None
    is_starter: bool
    is_bench: bool
    is_injured_reserve: bool
    is_nfl_bye: bool


@dataclass(frozen=True, slots=True)
class TeamWeekRoster:
    team_id: int
    team_name: str
    week: int
    players: tuple[PlayerWeekSnapshot, ...]
