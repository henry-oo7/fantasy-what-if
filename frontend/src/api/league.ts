import { queryOptions } from "@tanstack/react-query";

import { getJson, postJson } from "./client";
import { personalQueryKeys } from "./personalLeagues";
import type {
  AllPlayStanding,
  ImportedSeasonsResponse,
  ImportSeasonRequest,
  ImportSeasonResponse,
  LeagueSnapshot,
  PlayoffPredictorSnapshot,
} from "../types/league";

function viewedSeasonId(): number | null {
  if (typeof window === "undefined") {
    return null;
  }

  const value = new URLSearchParams(window.location.search).get("season_id");
  const seasonId = Number(value);

  return Number.isInteger(seasonId) && seasonId > 0 ? seasonId : null;
}

/** Legacy callers pass no Season; a personal Season is always explicit. */
export const leagueQueryKeys = {
  all: ["league"] as const,

  current: (seasonId?: number) =>
    seasonId === undefined
      ? ([...leagueQueryKeys.all, "current", viewedSeasonId()] as const)
      : ([...personalQueryKeys.season(seasonId), "snapshot"] as const),

  seasons: () => [...leagueQueryKeys.all, "seasons"] as const,

  allPlay: (seasonId?: number) =>
    seasonId === undefined
      ? ([...leagueQueryKeys.all, "all-play", viewedSeasonId()] as const)
      : ([...personalQueryKeys.season(seasonId), "all-play"] as const),

  playoffPredictor: (seasonId?: number) =>
    seasonId === undefined
      ? ([
          ...leagueQueryKeys.all,
          "playoff-predictor",
          viewedSeasonId(),
        ] as const)
      : ([...personalQueryKeys.season(seasonId), "playoff-predictor"] as const),
};

export function currentLeagueQueryOptions(seasonId?: number) {
  return queryOptions({
    queryKey: leagueQueryKeys.current(seasonId),
    queryFn: ({ signal }) =>
      getJson<LeagueSnapshot>(
        seasonId === undefined
          ? "/leagues/current"
          : `/seasons/${seasonId}/snapshot`,
        signal,
      ),
    staleTime: 60_000,
  });
}

export function allPlayQueryOptions(seasonId?: number) {
  return queryOptions({
    queryKey: leagueQueryKeys.allPlay(seasonId),
    queryFn: ({ signal }) =>
      getJson<AllPlayStanding[]>(
        seasonId === undefined
          ? "/analytics/all-play"
          : `/seasons/${seasonId}/analytics/all-play`,
        signal,
      ),
    staleTime: 60_000,
  });
}

export function playoffPredictorQueryOptions(seasonId?: number) {
  return queryOptions({
    queryKey: leagueQueryKeys.playoffPredictor(seasonId),
    queryFn: ({ signal }) =>
      getJson<PlayoffPredictorSnapshot>(
        seasonId === undefined
          ? "/leagues/current/playoff-predictor"
          : `/seasons/${seasonId}/playoff-predictor`,
        signal,
      ),
    staleTime: 60_000,
  });
}

export function getImportedSeasons(
  signal?: AbortSignal,
): Promise<ImportedSeasonsResponse> {
  return getJson<ImportedSeasonsResponse>(
    "/leagues/seasons",
    signal,
  );
}


export function importedSeasonsQueryOptions() {
  return queryOptions({
    queryKey: leagueQueryKeys.seasons(),
    queryFn: ({ signal }) =>
      getImportedSeasons(signal),
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
