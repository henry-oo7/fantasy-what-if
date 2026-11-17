"""Load canonical data for the playoff predictor."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    League,
    Matchup,
    ScheduleRule,
    Season,
    Team,
)
from app.domain.playoff_predictor import (
    PlayoffPredictorMatchup,
    PlayoffPredictorSnapshot,
    PlayoffPredictorTeam,
)
from app.domain.standings_tiebreaker import (
    resolve_standings_tiebreaker,
)


class PlayoffPredictorConfigurationError(RuntimeError):
    """The selected season lacks required playoff configuration."""


def load_playoff_predictor_snapshot(
    session: Session,
    *,
    season_id: int,
) -> PlayoffPredictorSnapshot | None:
    """Load one season without contacting its provider."""

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

    schedule_rule = session.scalar(
        select(ScheduleRule).where(
            ScheduleRule.season_id == season.id,
        )
    )

    if schedule_rule is None:
        raise PlayoffPredictorConfigurationError(
            "Schedule configuration is unavailable for the selected season."
        )

    if schedule_rule.playoff_team_count is None:
        raise PlayoffPredictorConfigurationError(
            "Playoff team count is unavailable for the "
            "selected season. Synchronize the season to "
            "import its playoff configuration."
        )

    effective_tiebreaker = resolve_standings_tiebreaker(
        provider_rule=schedule_rule.playoff_seed_tie_rule,
        admin_fallback_rule=(schedule_rule.playoff_seed_tie_rule_fallback),
    )

    if effective_tiebreaker is None:
        raise PlayoffPredictorConfigurationError(
            "A supported playoff seeding tiebreaker is unavailable "
            "for the selected season. Synchronize the season or ask "
            "an administrator to choose a fallback rule."
        )

    database_teams = session.scalars(
        select(Team)
        .where(
            Team.season_id == season.id,
            Team.is_active.is_(True),
        )
        .order_by(Team.id)
    ).all()

    teams = tuple(
        PlayoffPredictorTeam(
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

    team_ids = {team.team_id for team in teams}

    database_matchups = session.scalars(
        select(Matchup)
        .where(
            Matchup.season_id == season.id,
            Matchup.week <= schedule_rule.regular_season_weeks,
            Matchup.is_bye.is_(False),
            Matchup.away_team_id.is_not(None),
        )
        .order_by(
            Matchup.week,
            Matchup.home_team_id,
            Matchup.id,
        )
    ).all()

    completed_matchups: list[PlayoffPredictorMatchup] = []
    remaining_matchups: list[PlayoffPredictorMatchup] = []

    for matchup in database_matchups:
        if matchup.away_team_id is None:
            continue

        if matchup.home_team_id not in team_ids or matchup.away_team_id not in team_ids:
            raise RuntimeError(f"Matchup {matchup.id} references an inactive or missing team.")

        if matchup.is_complete and (matchup.home_score is None or matchup.away_score is None):
            raise RuntimeError(f"Completed matchup {matchup.id} has a missing score.")

        snapshot = PlayoffPredictorMatchup(
            matchup_id=matchup.id,
            week=matchup.week,
            home_team_id=matchup.home_team_id,
            away_team_id=matchup.away_team_id,
            home_score=(float(matchup.home_score) if matchup.home_score is not None else None),
            away_score=(float(matchup.away_score) if matchup.away_score is not None else None),
            is_complete=matchup.is_complete,
        )

        if matchup.is_complete:
            completed_matchups.append(snapshot)
        else:
            remaining_matchups.append(snapshot)

    return PlayoffPredictorSnapshot(
        league_id=league.id,
        league_name=league.name,
        season_id=season.id,
        season=season.year,
        current_week=season.current_week,
        regular_season_weeks=(schedule_rule.regular_season_weeks),
        playoff_team_count=(schedule_rule.playoff_team_count),
        playoff_seed_tie_rule=effective_tiebreaker.rule.value,
        playoff_seed_tie_rule_source=(effective_tiebreaker.source.value),
        allows_ties=schedule_rule.allows_ties,
        teams=teams,
        completed_matchups=tuple(completed_matchups),
        remaining_matchups=tuple(remaining_matchups),
    )
