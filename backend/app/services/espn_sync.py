from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.espn.client import create_espn_league
from app.repositories.league_repository import LeagueRepository
from app.services.sync_lock import acquire_sync_lock


@dataclass(frozen=True, slots=True)
class SyncResult:
    league_id: int
    season: int
    current_week: int
    full_sync: bool
    player_weeks: tuple[int, ...]
    teams_upserted: int
    matchups_upserted: int
    fantasy_byes_upserted: int
    player_week_scores_upserted: int


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None


def normalize_eligible_slots(value: Any) -> list[str]:
    if not value:
        return []

    return [str(slot) for slot in value]


def select_player_weeks(
    *,
    current_week: int,
    regular_season_weeks: int,
    full_sync: bool,
) -> tuple[int, ...]:
    last_completed_week = min(
        current_week - 1,
        regular_season_weeks,
    )

    if last_completed_week < 1:
        return ()

    if full_sync:
        return tuple(range(1, last_completed_week + 1))

    first_week = max(1, last_completed_week - 1)

    return tuple(range(first_week, last_completed_week + 1))


def synchronize_espn(
    *,
    session: Session,
    settings: Settings,
    full_sync: bool,
) -> SyncResult:
    acquire_sync_lock(
        session,
        espn_league_id=settings.espn_league_id,
        season=settings.espn_season,
    )

    espn_league = create_espn_league(settings)
    repository = LeagueRepository(session)

    current_week = int(espn_league.current_week)
    regular_season_weeks = int(espn_league.settings.reg_season_count)

    league = repository.upsert_league(
        espn_league_id=settings.espn_league_id,
        name=espn_league.settings.name,
    )

    season = repository.upsert_season(
        league_id=league.id,
        year=settings.espn_season,
        current_week=current_week,
        regular_season_weeks=regular_season_weeks,
    )

    teams_upserted = 0

    for espn_team in espn_league.teams:
        repository.upsert_team(
            season_id=season.id,
            espn_team_id=int(espn_team.team_id),
            name=str(espn_team.team_name),
            wins=int(espn_team.wins),
            losses=int(espn_team.losses),
            ties=int(espn_team.ties),
            points_for=float(espn_team.points_for),
            points_against=float(espn_team.points_against),
        )
        teams_upserted += 1

    session.flush()

    team_map = repository.get_team_map(season.id)

    player_weeks = select_player_weeks(
        current_week=current_week,
        regular_season_weeks=regular_season_weeks,
        full_sync=full_sync,
    )

    sync_run = repository.create_sync_run(
        season_id=season.id,
        is_full_sync=full_sync,
        requested_from_week=(min(player_weeks) if player_weeks else None),
        requested_through_week=(max(player_weeks) if player_weeks else None),
    )

    matchups_upserted = 0
    fantasy_byes_upserted = 0
    player_scores_upserted = 0

    try:
        for week in range(1, regular_season_weeks + 1):
            box_scores = list(espn_league.box_scores(week=week))

            repository.delete_week_matchups(
                season_id=season.id,
                week=week,
            )

            seen_espn_team_ids: set[int] = set()
            is_complete = week < current_week

            if is_complete and not box_scores:
                raise RuntimeError(f"Completed week {week} returned no box scores.")

            for box_score in box_scores:
                home_espn_team_id = int(box_score.home_team.team_id)
                away_espn_team_id = int(box_score.away_team.team_id)

                if home_espn_team_id in seen_espn_team_ids:
                    raise RuntimeError(
                        f"Team {home_espn_team_id} appears more than once in week {week}."
                    )

                if away_espn_team_id in seen_espn_team_ids:
                    raise RuntimeError(
                        f"Team {away_espn_team_id} appears more than once in week {week}."
                    )

                seen_espn_team_ids.add(home_espn_team_id)
                seen_espn_team_ids.add(away_espn_team_id)

                home_team = team_map.get(home_espn_team_id)
                away_team = team_map.get(away_espn_team_id)

                if home_team is None or away_team is None:
                    raise RuntimeError(f"Week {week} referenced an unknown team.")

                repository.add_matchup(
                    season_id=season.id,
                    week=week,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    home_score=(float(box_score.home_score) if is_complete else None),
                    away_score=(float(box_score.away_score) if is_complete else None),
                    is_complete=is_complete,
                    is_bye=False,
                )
                matchups_upserted += 1

            missing_espn_team_ids = set(team_map) - seen_espn_team_ids

            for missing_espn_team_id in sorted(missing_espn_team_ids):
                bye_team = team_map[missing_espn_team_id]

                repository.add_matchup(
                    season_id=season.id,
                    week=week,
                    home_team_id=bye_team.id,
                    away_team_id=None,
                    home_score=None,
                    away_score=None,
                    is_complete=is_complete,
                    is_bye=True,
                )
                matchups_upserted += 1
                fantasy_byes_upserted += 1

            if week not in player_weeks:
                continue

            repository.delete_player_week_scores(
                season_id=season.id,
                week=week,
            )

            for box_score in box_scores:
                lineup_groups = (
                    (
                        int(box_score.home_team.team_id),
                        box_score.home_lineup,
                    ),
                    (
                        int(box_score.away_team.team_id),
                        box_score.away_lineup,
                    ),
                )

                for espn_team_id, lineup in lineup_groups:
                    database_team = team_map.get(espn_team_id)

                    if database_team is None:
                        raise RuntimeError(
                            f"Week {week} lineup referenced unknown team {espn_team_id}."
                        )

                    seen_player_ids: set[int] = set()

                    for player in lineup:
                        espn_player_id = int(player.playerId)

                        if espn_player_id in seen_player_ids:
                            raise RuntimeError(
                                f"Player {espn_player_id} appears "
                                f"more than once for team "
                                f"{espn_team_id} in week {week}."
                            )

                        seen_player_ids.add(espn_player_id)

                        repository.add_player_week_score(
                            season_id=season.id,
                            team_id=database_team.id,
                            week=week,
                            espn_player_id=espn_player_id,
                            player_name=str(player.name),
                            pro_team=normalize_optional_text(getattr(player, "proTeam", None)),
                            default_position=str(player.position),
                            actual_lineup_slot=str(player.lineupSlot),
                            eligible_lineup_slots=(
                                normalize_eligible_slots(
                                    getattr(
                                        player,
                                        "eligibleSlots",
                                        [],
                                    )
                                )
                            ),
                            points=float(player.points),
                            projected_points=(
                                float(player.projected_points)
                                if getattr(
                                    player,
                                    "projected_points",
                                    None,
                                )
                                is not None
                                else None
                            ),
                            injury_status=normalize_optional_text(
                                getattr(
                                    player,
                                    "injuryStatus",
                                    None,
                                )
                            ),
                            active_status=normalize_optional_text(
                                getattr(
                                    player,
                                    "active_status",
                                    None,
                                )
                            ),
                            on_bye_week=bool(
                                getattr(
                                    player,
                                    "on_bye_week",
                                    False,
                                )
                            ),
                        )
                        player_scores_upserted += 1

            session.flush()

        repository.complete_sync_run(
            sync_run,
            teams_upserted=teams_upserted,
            matchups_upserted=matchups_upserted,
            player_week_scores_upserted=(player_scores_upserted),
        )

        session.commit()

    except Exception as exc:
        session.rollback()

        failed_repository = LeagueRepository(session)

        persisted_sync_run = session.get(
            type(sync_run),
            sync_run.id,
        )

        if persisted_sync_run is not None:
            failed_repository.fail_sync_run(
                persisted_sync_run,
                safe_error_message=(f"{type(exc).__name__}: {str(exc)[:900]}"),
            )
            session.commit()

        raise

    return SyncResult(
        league_id=settings.espn_league_id,
        season=settings.espn_season,
        current_week=current_week,
        full_sync=full_sync,
        player_weeks=player_weeks,
        teams_upserted=teams_upserted,
        matchups_upserted=matchups_upserted,
        fantasy_byes_upserted=fantasy_byes_upserted,
        player_week_scores_upserted=player_scores_upserted,
    )
