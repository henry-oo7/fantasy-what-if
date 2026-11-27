"""Personal League and Season routes (ADR 0010).

Additive and transitional: the workspace ``/api/leagues/*`` and
``/api/analytics/*`` routes keep their claim-backed authority unchanged.
Every Season route authorizes through ``require_league_season_access``
first, in the same transaction that then loads only that Season's data and
hands it to the existing analytics. Missing, inaccessible and revoked
Seasons return one identical 404.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.analytics.all_play import AllPlayStanding, calculate_all_play_standings
from app.analytics.league_average import (
    LeagueAverageStanding,
    calculate_league_average_standings,
)
from app.analytics.optimal_lineup import TeamOptimalLineupSummary
from app.analytics.optimal_lineup_breakdown import TeamOptimalLineupBreakdown
from app.analytics.schedule_swap import ScheduleSwapResult
from app.api.analytics import schedule_swap_or_422, team_optimal_lineup_breakdown
from app.api.auth_dependencies import (
    AuthenticationContext,
    get_personal_authentication_context,
    require_session_csrf,
)
from app.api.security import validate_request_origin
from app.db.session import SessionFactory
from app.domain.league import LeagueSnapshot
from app.domain.playoff_predictor import PlayoffPredictorSnapshot
from app.repositories.league_repository import load_league_snapshot
from app.repositories.optimal_lineup_repository import load_optimal_lineup_summaries
from app.repositories.playoff_predictor_repository import (
    PlayoffPredictorConfigurationError,
    load_playoff_predictor_snapshot,
)
from app.schemas.leagues import PlayoffPredictorResponse
from app.schemas.personal_leagues import (
    DefaultSeasonRequest,
    DefaultSeasonResponse,
    PersonalLeagueResponse,
    PersonalLeaguesResponse,
    PersonalSeasonDetailResponse,
    PersonalSeasonResponse,
)
from app.services.league_season_authorization import (
    AuthorizedLeagueSeason,
    LeagueSeasonDataUnavailableError,
    LeagueSeasonNotFoundError,
    list_readable_league_seasons,
    readable_default_season_id,
    require_league_season_access,
    set_default_season,
)

SEASON_NOT_FOUND_DETAIL = "League season not found."
SEASON_DATA_UNAVAILABLE_DETAIL = "League season data is not available yet."

router = APIRouter(prefix="/api", tags=["personal-leagues"])

PersonalContext = Annotated[AuthenticationContext, Depends(get_personal_authentication_context)]
SeasonId = Annotated[int, Path(gt=0)]


def _read_season[T](
    context: AuthenticationContext,
    season_id: int,
    read: Callable[[Session, AuthorizedLeagueSeason], T],
) -> T:
    """Authorize, then read, in one transaction that holds the access-row share lock."""

    try:
        with SessionFactory() as session:
            return read(
                session,
                require_league_season_access(session, user_id=context.user_id, season_id=season_id),
            )
    except LeagueSeasonNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SEASON_NOT_FOUND_DETAIL) from None
    except LeagueSeasonDataUnavailableError:
        raise HTTPException(status.HTTP_409_CONFLICT, SEASON_DATA_UNAVAILABLE_DETAIL) from None


def _snapshot(session: Session, authorized: AuthorizedLeagueSeason) -> LeagueSnapshot:
    authorized.require_data()
    snapshot = load_league_snapshot(session, season_id=authorized.season.id)

    if snapshot is None:
        raise LeagueSeasonDataUnavailableError

    return snapshot


def _optimal_lineups(
    session: Session, authorized: AuthorizedLeagueSeason
) -> tuple[TeamOptimalLineupSummary, ...]:
    authorized.require_data()
    summaries = load_optimal_lineup_summaries(session, season_id=authorized.season.id)

    if not summaries:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Optimal-lineup analysis has not been generated yet."
        )

    return summaries


def _season_fields(authorized: AuthorizedLeagueSeason) -> dict[str, object]:
    season = authorized.season
    return {
        "season_id": season.id,
        "season_year": season.year,
        "access_status": authorized.access.status.value,
        "sync_state": season.sync_state,
        "data_available": authorized.data_available,
        "last_successful_sync_at": season.last_successful_sync_at,
    }


@router.get("/leagues", response_model=PersonalLeaguesResponse)
def read_personal_leagues(context: PersonalContext) -> PersonalLeaguesResponse:
    """The Leagues and Seasons this user may read, grouped by League."""

    with SessionFactory() as session:
        readable = list_readable_league_seasons(session, user_id=context.user_id)
        default_season_id = readable_default_season_id(
            session, user_id=context.user_id, readable=readable
        )
        leagues: dict[int, PersonalLeagueResponse] = {}

        for item in readable:
            leagues.setdefault(
                item.league.id,
                PersonalLeagueResponse(
                    provider=item.league.provider,
                    provider_league_id=item.league.provider_league_id,
                    league_name=item.league.name,
                    seasons=[],
                ),
            ).seasons.append(PersonalSeasonResponse(**_season_fields(item)))

    return PersonalLeaguesResponse(
        default_season_id=default_season_id, leagues=list(leagues.values())
    )


@router.get("/seasons/{season_id}", response_model=PersonalSeasonDetailResponse)
def read_personal_season(context: PersonalContext, season_id: SeasonId):
    """Safe access and synchronization state for one Season."""

    return _read_season(
        context,
        season_id,
        lambda _, item: PersonalSeasonDetailResponse(
            **_season_fields(item),
            provider=item.league.provider,
            provider_league_id=item.league.provider_league_id,
            league_name=item.league.name,
        ),
    )


@router.get("/seasons/{season_id}/snapshot")
def read_personal_season_snapshot(context: PersonalContext, season_id: SeasonId) -> LeagueSnapshot:
    return _read_season(context, season_id, _snapshot)


@router.get("/seasons/{season_id}/playoff-predictor", response_model=PlayoffPredictorResponse)
def read_personal_playoff_predictor(
    context: PersonalContext, season_id: SeasonId
) -> PlayoffPredictorSnapshot:
    def read(session: Session, authorized: AuthorizedLeagueSeason) -> PlayoffPredictorSnapshot:
        authorized.require_data()

        try:
            snapshot = load_playoff_predictor_snapshot(session, season_id=authorized.season.id)
        except PlayoffPredictorConfigurationError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

        if snapshot is None:
            raise LeagueSeasonDataUnavailableError

        return snapshot

    return _read_season(context, season_id, read)


@router.get("/seasons/{season_id}/analytics/all-play")
def read_personal_all_play(
    context: PersonalContext, season_id: SeasonId
) -> tuple[AllPlayStanding, ...]:
    return calculate_all_play_standings(_read_season(context, season_id, _snapshot))


@router.get("/seasons/{season_id}/analytics/league-average")
def read_personal_league_average(
    context: PersonalContext, season_id: SeasonId
) -> tuple[LeagueAverageStanding, ...]:
    return calculate_league_average_standings(_read_season(context, season_id, _snapshot))


@router.get("/seasons/{season_id}/analytics/schedule-swap")
def read_personal_schedule_swap(
    context: PersonalContext,
    season_id: SeasonId,
    first_team_id: Annotated[int, Query(gt=0)],
    second_team_id: Annotated[int, Query(gt=0)],
) -> ScheduleSwapResult:
    return schedule_swap_or_422(
        _read_season(context, season_id, _snapshot), first_team_id, second_team_id
    )


@router.get("/seasons/{season_id}/analytics/optimal-lineups")
def read_personal_optimal_lineups(
    context: PersonalContext, season_id: SeasonId
) -> tuple[TeamOptimalLineupSummary, ...]:
    return _read_season(context, season_id, _optimal_lineups)


@router.get("/seasons/{season_id}/analytics/optimal-lineups/{team_id}")
def read_personal_team_optimal_lineup(
    context: PersonalContext, season_id: SeasonId, team_id: int
) -> TeamOptimalLineupBreakdown:
    summaries, snapshot = _read_season(
        context,
        season_id,
        lambda session, authorized: (
            _optimal_lineups(session, authorized),
            _snapshot(session, authorized),
        ),
    )
    return team_optimal_lineup_breakdown(team_id, summaries, snapshot)


@router.put("/preferences/default-season", response_model=DefaultSeasonResponse)
def update_default_season(
    payload: DefaultSeasonRequest,
    context: PersonalContext,
    _: Annotated[None, Depends(validate_request_origin)],
    __: Annotated[AuthenticationContext, Depends(require_session_csrf)],
) -> DefaultSeasonResponse:
    """Make one readable Season this user's default; only the caller's own preference changes."""

    try:
        with SessionFactory() as session:
            set_default_season(session, user_id=context.user_id, season_id=payload.season_id)
            session.commit()
    except LeagueSeasonNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SEASON_NOT_FOUND_DETAIL) from None

    return DefaultSeasonResponse(default_season_id=payload.season_id)
