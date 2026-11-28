import { PERSONAL_USER, createApiMock, signInAs } from "./accountHarness";
import { seasonDetail, snapshot, twoLeagues } from "./personalFixtures";
import type { PersonalLeagues } from "../types/personal";

/** Mocked personal API: every Season's status and snapshot answer from `leagues`. */
export function mockPersonalApi(leagues: PersonalLeagues = twoLeagues()) {
  const api = createApiMock();
  signInAs(api, PERSONAL_USER);
  api.reply("GET /api/leagues", 200, leagues);

  for (const league of leagues.leagues) {
    for (const item of league.seasons) {
      api.reply(`GET /api/seasons/${item.season_id}`, 200, seasonDetail(leagues, item.season_id));
      api.reply(
        `GET /api/seasons/${item.season_id}/snapshot`,
        200,
        snapshot(league.league_name, item.season_year),
      );
      api.reply(`GET /api/seasons/${item.season_id}/analytics/league-average`, 200, []);
    }
  }

  return api;
}
