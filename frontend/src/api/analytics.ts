import {
  queryOptions,
} from "@tanstack/react-query";

import { getJson } from "./client";
import type {
  LeagueAverageStanding,
  ScheduleSwapResult,
} from "../types/analytics";


export const analyticsQueryKeys = {
  all: ["analytics"] as const,

  leagueAverage: () =>
    [
      ...analyticsQueryKeys.all,
      "league-average",
    ] as const,

  scheduleSwap: (
    firstTeamId: number,
    secondTeamId: number,
  ) =>
    [
      ...analyticsQueryKeys.all,
      "schedule-swap",
      firstTeamId,
      secondTeamId,
    ] as const,
};

export function scheduleSwapQueryOptions(
  firstTeamId: number,
  secondTeamId: number,
) {
  const search = new URLSearchParams({
    first_team_id: String(firstTeamId),
    second_team_id: String(secondTeamId),
  });

  return queryOptions({
    queryKey: analyticsQueryKeys.scheduleSwap(
      firstTeamId,
      secondTeamId,
    ),
    queryFn: ({ signal }) =>
      getJson<ScheduleSwapResult>(
        `/analytics/schedule-swap?${search.toString()}`,
        signal,
      ),
    enabled:
      firstTeamId > 0 &&
      secondTeamId > 0 &&
      firstTeamId !== secondTeamId,
    staleTime: 60_000,
  });
}


export function leagueAverageQueryOptions() {
  return queryOptions({
    queryKey: analyticsQueryKeys.leagueAverage(),
    queryFn: ({ signal }) =>
      getJson<LeagueAverageStanding[]>(
        "/analytics/league-average",
        signal,
      ),
    staleTime: 60_000,
  });
}

