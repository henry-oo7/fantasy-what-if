from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    Matchup,
    Season,
    Team,
)
from app.domain.league import LeagueSnapshot, MatchupSnapshot, TeamSnapshot


def load_league_snapshot(
    session: Session,
    *,
    season_id: int,
) -> LeagueSnapshot | None:
    """Load one season using canonical internal identities."""

    season = session.get(
        Season,
        season_id,
    )

    if season is None:
        return None

    league = session.get(
        League,
        season.league_id,
    )

    if league is None:
        raise RuntimeError(f"Season {season.id} references a missing league.")

    database_teams = session.scalars(
        select(Team).where(Team.season_id == season.id).order_by(Team.id)
    ).all()

    teams = tuple(
        TeamSnapshot(
            team_id=team.id,
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
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                home_score=float(matchup.home_score),
                away_score=float(matchup.away_score),
            )
        )

    return LeagueSnapshot(
        league_id=league.id,
        name=league.name,
        season=season.year,
        current_week=season.current_week,
        regular_season_weeks=(season.regular_season_weeks),
        teams=teams,
        matchups=tuple(matchups),
    )
