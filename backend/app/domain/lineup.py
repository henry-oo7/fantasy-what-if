from dataclasses import dataclass

DEFAULT_FOOTBALL_STARTING_SLOTS = (
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "K",
    "DST",
)


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
    starting_slots: tuple[str, ...] = DEFAULT_FOOTBALL_STARTING_SLOTS

    def __post_init__(self) -> None:
        normalized_slots = tuple(
            str(slot).strip() for slot in self.starting_slots if str(slot).strip()
        )

        if not normalized_slots:
            raise ValueError("starting_slots must contain at least one configured lineup slot.")

        object.__setattr__(
            self,
            "starting_slots",
            normalized_slots,
        )
