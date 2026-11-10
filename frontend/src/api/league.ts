import { queryOptions } from "@tanstack/react-query";

import { getJson, postJson } from "./client";
import type {
  AllPlayStanding,
  ImportedSeasonsResponse,
  ImportSeasonRequest,
  ImportSeasonResponse,
  LeagueSnapshot,
} from "../types/league";

function viewedSeasonId(): number | null {
  if (typeof window === "undefined") {
    return null;
  }

  const value = new URLSearchParams(window.location.search).get("season_id");
  const seasonId = Number(value);

  return Number.isInteger(seasonId) && seasonId > 0 ? seasonId : null;
}

export const leagueQueryKeys = {
  all: ["league"] as const,

  current: () => [...leagueQueryKeys.all, "current", viewedSeasonId()] as const,

  seasons: () => [...leagueQueryKeys.all, "seasons"] as const,

  allPlay: () =>
    [...leagueQueryKeys.all, "all-play", viewedSeasonId()] as const,
};

export function currentLeagueQueryOptions() {
  return queryOptions({
    queryKey: leagueQueryKeys.current(),
    queryFn: ({ signal }) =>
      getJson<LeagueSnapshot>("/leagues/current", signal),
    staleTime: 60_000,
  });
}

export function allPlayQueryOptions() {
  return queryOptions({
    queryKey: leagueQueryKeys.allPlay(),
    queryFn: ({ signal }) =>
      getJson<AllPlayStanding[]>("/analytics/all-play", signal),
    staleTime: 60_000,
  });
}

export function importedSeasonsQueryOptions() {
  return queryOptions({
    queryKey: leagueQueryKeys.seasons(),
    queryFn: ({ signal }) =>
      getJson<ImportedSeasonsResponse>("/leagues/seasons", signal),
    staleTime: 60_000,
  });
}

export function importSeason(
  request: ImportSeasonRequest,
  csrfToken: string,
): Promise<ImportSeasonResponse> {
  return postJson<ImportSeasonResponse>("/leagues/seasons/import", request, {
    csrfToken,
  });
}
