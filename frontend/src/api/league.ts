import { queryOptions } from "@tanstack/react-query";

import { getJson } from "./client";
import type {
  AllPlayStanding,
  ImportedSeasonsResponse,
  LeagueSnapshot,
} from "../types/league";

export const leagueQueryKeys = {
  all: ["league"] as const,

  current: () =>
    [
      ...leagueQueryKeys.all,
      "current",
    ] as const,

  seasons: () =>
    [
      ...leagueQueryKeys.all,
      "seasons",
    ] as const,

  allPlay: () =>
    [
      ...leagueQueryKeys.all,
      "all-play",
    ] as const,
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
      getJson<ImportedSeasonsResponse>(
        "/leagues/seasons",
        signal,
      ),
    staleTime: 60_000,
  });
}

