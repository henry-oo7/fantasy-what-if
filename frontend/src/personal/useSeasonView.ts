import { useQuery } from "@tanstack/react-query";

import { importedSeasonsQueryOptions } from "../api/league";
import { personalSeasonStatusQueryOptions } from "../api/personalLeagues";
import type { ImportedSeason, LeagueSnapshot } from "../types/league";
import { completedWeekCount, seasonPresentation } from "../utils/seasonPresentation";
import { useSeasonScope } from "./seasonScope";

/**
 * Season presentation and last-sync time for the viewed Season: from the
 * workspace Season list on legacy routes, and from the personal Season
 * status plus the snapshot's own week counts on personal routes.
 */
export function useSeasonView() {
  const { seasonId } = useSeasonScope();
  const personal = seasonId !== undefined;
  const legacyQuery = useQuery({
    ...importedSeasonsQueryOptions(),
    enabled: !personal,
  });
  const personalQuery = useQuery({
    ...personalSeasonStatusQueryOptions(seasonId ?? 0),
    enabled: personal,
  });

  function forLeague(league: LeagueSnapshot) {
    let viewedSeason: ImportedSeason | undefined;

    if (personal) {
      // A Season whose regular season is fully recorded reads as completed.
      const complete = completedWeekCount(league) >= league.regular_season_weeks;
      viewedSeason = {
        season_id: seasonId,
        league_id: league.league_id,
        league_name: league.name,
        year: league.season,
        status: complete ? "completed" : "active",
        is_active: !complete,
        last_synced_at: personalQuery.data?.last_successful_sync_at ?? null,
      };
    } else {
      viewedSeason = legacyQuery.data?.seasons.find(
        (season) => season.year === league.season,
      );
    }

    return {
      seasonView: seasonPresentation(league, viewedSeason),
      lastSyncedAt: viewedSeason?.last_synced_at ?? null,
    };
  }

  return {
    forLeague,
    pending: personal ? personalQuery.isPending : legacyQuery.isPending,
  };
}
