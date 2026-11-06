from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    Matchup,
    PlayerWeekScore,
    Season,
    SyncRun,
    SyncStatus,
    Team,
)
from app.domain.league import LeagueSnapshot, MatchupSnapshot, TeamSnapshot


class LeagueRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_league(
        self,
        *,
        espn_league_id: int,
        name: str,
    ) -> League:
        statement = (
            insert(League)
            .values(
                espn_league_id=espn_league_id,
                name=name,
            )
            .on_conflict_do_update(
                index_elements=[League.espn_league_id],
                set_={
                    "name": name,
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(League.id)
        )

        league_id = self.session.execute(statement).scalar_one()

        return self.session.get_one(League, league_id)

    def upsert_season(
        self,
        *,
        league_id: int,
        year: int,
        current_week: int,
        regular_season_weeks: int,
    ) -> Season:
        statement = (
            insert(Season)
            .values(
                league_id=league_id,
                year=year,
                current_week=current_week,
                regular_season_weeks=regular_season_weeks,
            )
            .on_conflict_do_update(
                constraint="uq_seasons_league_year",
                set_={
                    "current_week": current_week,
                    "regular_season_weeks": regular_season_weeks,
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(Season.id)
        )

        season_id = self.session.execute(statement).scalar_one()

        return self.session.get_one(Season, season_id)

    def upsert_team(
        self,
        *,
        season_id: int,
        espn_team_id: int,
        name: str,
        wins: int,
        losses: int,
        ties: int,
        points_for: float,
        points_against: float,
    ) -> Team:
        statement = (
            insert(Team)
            .values(
                season_id=season_id,
                espn_team_id=espn_team_id,
                name=name,
                wins=wins,
                losses=losses,
                ties=ties,
                points_for=Decimal(str(points_for)),
                points_against=Decimal(str(points_against)),
            )
            .on_conflict_do_update(
                constraint="uq_teams_season_espn_team",
                set_={
                    "name": name,
                    "wins": wins,
                    "losses": losses,
                    "ties": ties,
                    "points_for": Decimal(str(points_for)),
                    "points_against": Decimal(str(points_against)),
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(Team.id)
        )

        team_id = self.session.execute(statement).scalar_one()

        return self.session.get_one(Team, team_id)

    def get_team_map(self, season_id: int) -> dict[int, Team]:
        teams = self.session.scalars(
            select(Team).where(Team.season_id == season_id).order_by(Team.espn_team_id)
        ).all()

        return {team.espn_team_id: team for team in teams}

    def delete_week_matchups(
        self,
        *,
        season_id: int,
        week: int,
    ) -> None:
        self.session.execute(
            delete(Matchup).where(
                Matchup.season_id == season_id,
                Matchup.week == week,
            )
        )

    def add_matchup(
        self,
        *,
        season_id: int,
        week: int,
        home_team_id: int,
        away_team_id: int | None,
        home_score: float | None,
        away_score: float | None,
        is_complete: bool,
        is_bye: bool,
    ) -> Matchup:
        matchup = Matchup(
            season_id=season_id,
            week=week,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_score=(Decimal(str(home_score)) if home_score is not None else None),
            away_score=(Decimal(str(away_score)) if away_score is not None else None),
            is_complete=is_complete,
            is_bye=is_bye,
        )

        self.session.add(matchup)

        return matchup

    def delete_player_week_scores(
        self,
        *,
        season_id: int,
        week: int,
    ) -> None:
        self.session.execute(
            delete(PlayerWeekScore).where(
                PlayerWeekScore.season_id == season_id,
                PlayerWeekScore.week == week,
            )
        )

    def add_player_week_score(
        self,
        *,
        season_id: int,
        team_id: int,
        week: int,
        espn_player_id: int,
        player_name: str,
        pro_team: str | None,
        default_position: str,
        actual_lineup_slot: str,
        eligible_lineup_slots: list[str],
        points: float,
        projected_points: float | None,
        injury_status: str | None,
        active_status: str | None,
        on_bye_week: bool,
    ) -> PlayerWeekScore:
        is_bench = actual_lineup_slot == "BE"
        is_injured_reserve = actual_lineup_slot == "IR"
        is_starter = not is_bench and not is_injured_reserve

        player_score = PlayerWeekScore(
            season_id=season_id,
            team_id=team_id,
            week=week,
            espn_player_id=espn_player_id,
            player_name=player_name,
            pro_team=pro_team,
            default_position=default_position,
            actual_lineup_slot=actual_lineup_slot,
            eligible_lineup_slots=eligible_lineup_slots,
            points=Decimal(str(points)),
            projected_points=(
                Decimal(str(projected_points)) if projected_points is not None else None
            ),
            was_on_roster=True,
            is_starter=is_starter,
            is_bench=is_bench,
            is_injured_reserve=is_injured_reserve,
            raw_metadata={
                "injury_status": injury_status,
                "active_status": active_status,
                "on_bye_week": on_bye_week,
            },
        )

        self.session.add(player_score)

        return player_score

    def create_sync_run(
        self,
        *,
        season_id: int,
        is_full_sync: bool,
        requested_from_week: int | None,
        requested_through_week: int | None,
    ) -> SyncRun:
        sync_run = SyncRun(
            season_id=season_id,
            status=SyncStatus.RUNNING,
            is_full_sync=is_full_sync,
            requested_from_week=requested_from_week,
            requested_through_week=requested_through_week,
        )

        self.session.add(sync_run)
        self.session.flush()

        return sync_run

    def complete_sync_run(
        self,
        sync_run: SyncRun,
        *,
        teams_upserted: int,
        matchups_upserted: int,
        player_week_scores_upserted: int,
    ) -> None:
        sync_run.status = SyncStatus.SUCCEEDED
        sync_run.completed_at = datetime.now(UTC)
        sync_run.teams_upserted = teams_upserted
        sync_run.matchups_upserted = matchups_upserted
        sync_run.player_week_scores_upserted = player_week_scores_upserted

        if sync_run.season is not None:
            sync_run.season.last_synced_at = datetime.now(UTC)

    def fail_sync_run(
        self,
        sync_run: SyncRun,
        *,
        safe_error_message: str,
    ) -> None:
        sync_run.status = SyncStatus.FAILED
        sync_run.completed_at = datetime.now(UTC)
        sync_run.safe_error_message = safe_error_message[:1000]


def load_league_snapshot(
    session: Session,
    *,
    espn_league_id: int,
    year: int,
) -> LeagueSnapshot | None:
    season = session.scalar(
        select(Season)
        .join(League, Season.league_id == League.id)
        .where(
            League.espn_league_id == espn_league_id,
            Season.year == year,
        )
    )

    if season is None:
        return None

    league = session.get_one(League, season.league_id)

    database_teams = session.scalars(
        select(Team).where(Team.season_id == season.id).order_by(Team.espn_team_id)
    ).all()

    teams = tuple(
        TeamSnapshot(
            team_id=team.espn_team_id,
            name=team.name,
            wins=team.wins,
            losses=team.losses,
            ties=team.ties,
            points_for=float(team.points_for),
            points_against=float(team.points_against),
        )
        for team in database_teams
    )

    database_matchups = session.scalars(
        select(Matchup)
        .where(
            Matchup.season_id == season.id,
            Matchup.is_complete.is_(True),
            Matchup.is_bye.is_(False),
            Matchup.away_team_id.is_not(None),
            Matchup.home_score.is_not(None),
            Matchup.away_score.is_not(None),
        )
        .order_by(
            Matchup.week,
            Matchup.home_team_id,
        )
    ).all()

    database_team_by_id = {team.id: team for team in database_teams}

    matchups: list[MatchupSnapshot] = []

    for matchup in database_matchups:
        home_team = database_team_by_id.get(matchup.home_team_id)
        away_team = database_team_by_id.get(matchup.away_team_id)

        if home_team is None or away_team is None:
            raise RuntimeError(f"Stored matchup {matchup.id} references a missing team.")

        if matchup.home_score is None or matchup.away_score is None:
            raise RuntimeError(f"Completed matchup {matchup.id} has a missing score.")

        matchups.append(
            MatchupSnapshot(
                week=matchup.week,
                home_team_id=home_team.espn_team_id,
                away_team_id=away_team.espn_team_id,
                home_score=float(matchup.home_score),
                away_score=float(matchup.away_score),
            )
        )

    return LeagueSnapshot(
        league_id=league.espn_league_id,
        name=league.name,
        season=season.year,
        current_week=season.current_week,
        regular_season_weeks=season.regular_season_weeks,
        teams=teams,
        matchups=tuple(matchups),
    )
