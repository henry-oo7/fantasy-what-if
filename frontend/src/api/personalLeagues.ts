import { queryOptions } from "@tanstack/react-query";

import { getJson, postJson, putJson } from "./client";
import type {
  DefaultSeasonResponse,
  LeagueImportRequest,
  LeagueImportResponse,
  PersonalLeagues,
  PersonalSeasonDetail,
} from "../types/personal";

/**
 * Every personal key lives under ["personal"]. The whole client cache is
 * purged whenever the signed-in identity changes or the session ends
 * (AuthProvider), so the account boundary is the cache's lifetime; every
 * Season-scoped key adds the season_id so Seasons never share an entry.
 */
export const personalQueryKeys = {
  all: ["personal"] as const,
  leagues: () => [...personalQueryKeys.all, "leagues"] as const,
  season: (seasonId: number) =>
    [...personalQueryKeys.all, "season", seasonId] as const,
  seasonStatus: (seasonId: number) =>
    [...personalQueryKeys.season(seasonId), "status"] as const,
};

export function personalLeaguesQueryOptions() {
  return queryOptions({
    queryKey: personalQueryKeys.leagues(),
    queryFn: ({ signal }) => getJson<PersonalLeagues>("/leagues", signal),
    staleTime: 30_000,
  });
}

/** Polling is bounded and owned by useSeasonPolling, not by the query. */
export function personalSeasonStatusQueryOptions(seasonId: number) {
  return queryOptions({
    queryKey: personalQueryKeys.seasonStatus(seasonId),
    queryFn: ({ signal }) =>
      getJson<PersonalSeasonDetail>(`/seasons/${seasonId}`, signal),
    retry: false,
  });
}

/**
 * Deliberately not a React Query mutation: the mutation cache would retain
 * the submitted credentials as variables. The body travels only in this POST.
 */
export function importLeague(
  request: LeagueImportRequest,
  csrfToken: string | null,
): Promise<LeagueImportResponse> {
  return postJson<LeagueImportResponse>("/league-imports", request, {
    csrfToken,
  });
}

export function setDefaultSeason(
  seasonId: number,
  csrfToken: string | null,
): Promise<DefaultSeasonResponse> {
  return putJson<DefaultSeasonResponse>(
    "/preferences/default-season",
    { season_id: seasonId },
    { csrfToken },
  );
}

export interface LeagueRemovedResponse {
  detail: string;
  code: "league_access_removed";
}

/**
 * Removes only this account's access to one League. Outcomes are shown in
 * the form and must not change global authentication state.
 */
export function removeLeague(
  providerLeagueId: string,
  confirmation: string,
  csrfToken: string | null,
): Promise<LeagueRemovedResponse> {
  return postJson<LeagueRemovedResponse>(
    `/leagues/espn/${encodeURIComponent(providerLeagueId)}/removal`,
    { confirmation },
    { csrfToken, reportErrors: false },
  );
}
