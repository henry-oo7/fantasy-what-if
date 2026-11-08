import {
  queryOptions,
} from "@tanstack/react-query";

import { getJson } from "./client";
import type {
  LeagueAverageStanding,
  ScheduleSwapResult,
  TeamOptimalLineupSummary,
} from "../types/analytics";


export const analyticsQueryKeys = {
  all: ["analytics"] as const,

  leagueAverage: () =>
    [
      ...analyticsQueryKeys.all,
      "league-average",
    ] as const,

  optimalLineups: () =>
    [
      ...analyticsQueryKeys.all,
      "optimal-lineups",
    ] as const,

  optimalLineupTeam: (teamId: number) =>
    [
      ...analyticsQueryKeys.optimalLineups(),
      teamId,
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


export function optimalLineupsQueryOptions() {
  return queryOptions({
    queryKey: analyticsQueryKeys.optimalLineups(),
    queryFn: ({ signal }) =>
      getJson<TeamOptimalLineupSummary[]>(
        "/analytics/optimal-lineups",
        signal,
      ),
    staleTime: 60_000,
  });
}


export function teamOptimalLineupQueryOptions(
  teamId: number,
) {
  return queryOptions({
    queryKey:
      analyticsQueryKeys.optimalLineupTeam(
        teamId,
      ),
    queryFn: ({ signal }) =>
      getJson<TeamOptimalLineupSummary>(
        `/analytics/optimal-lineups/${teamId}`,
        signal,
      ),
    enabled: teamId > 0,
    staleTime: 60_000,
  });
}

