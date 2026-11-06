from espn_api.football import League

from app.core.settings import Settings


def create_espn_league(settings: Settings) -> League:
    return League(
        league_id=settings.espn_league_id,
        year=settings.espn_season,
        espn_s2=settings.espn_s2.get_secret_value(),
        swid=settings.espn_swid.get_secret_value(),
    )
