import type { LeagueSnapshot } from "../types/league";
import type {
  PersonalLeagues,
  PersonalSeason,
  PersonalSeasonDetail,
} from "../types/personal";

/** Synthetic personal-League fixtures. No real provider data or credentials. */
export function season(
  seasonId: number,
  seasonYear: number,
  overrides: Partial<PersonalSeason> = {},
): PersonalSeason {
  return {
    season_id: seasonId,
    season_year: seasonYear,
    access_status: "active",
    sync_state: "fresh",
    data_available: true,
    last_successful_sync_at: "2025-01-02T03:04:05Z",
    ...overrides,
  };
}

/** Alpha League (2025 default, 2024) and Beta League (2025). */
export function twoLeagues(
  overrides: Partial<PersonalLeagues> = {},
): PersonalLeagues {
  return {
    default_season_id: 11,
    leagues: [
      {
        provider: "espn",
        provider_league_id: "1001",
        league_name: "Alpha League",
        seasons: [season(11, 2025), season(10, 2024)],
      },
      {
        provider: "espn",
        provider_league_id: "1002",
        league_name: "Beta League",
        seasons: [season(21, 2025)],
      },
    ],
    ...overrides,
  };
}

export function seasonDetail(
  leagues: PersonalLeagues,
  seasonId: number,
): PersonalSeasonDetail {
  for (const league of leagues.leagues) {
    const found = league.seasons.find((item) => item.season_id === seasonId);

    if (found) {
      return {
        ...found,
        provider: league.provider,
        provider_league_id: league.provider_league_id,
        league_name: league.league_name,
      };
    }
  }

  throw new Error(`No fixture Season ${seasonId}.`);
}

/** A one-matchup snapshot whose team names identify the League and year. */
export function snapshot(leagueName: string, year: number): LeagueSnapshot {
  return {
    league_id: year,
    name: leagueName,
    season: year,
    current_week: 2,
    regular_season_weeks: 14,
    teams: [
      { team_id: 1, name: `${leagueName} ${year} Rockets`, wins: 1, losses: 0, ties: 0, points_for: 110, points_against: 90 },
      { team_id: 2, name: `${leagueName} ${year} Comets`, wins: 0, losses: 1, ties: 0, points_for: 90, points_against: 110 },
    ],
    matchups: [
      { week: 1, home_team_id: 1, away_team_id: 2, home_score: 110, away_score: 90 },
    ],
  };
}
