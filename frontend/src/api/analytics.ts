import { queryOptions } from "@tanstack/react-query";

import { getJson } from "./client";
import { personalQueryKeys } from "./personalLeagues";
import type {
  LeagueAverageStanding,
  ScheduleSwapResult,
  TeamOptimalLineupBreakdown,
  TeamOptimalLineupSummary,
} from "../types/analytics";

function viewedSeasonKey(): readonly number[] {
  if (typeof window === "undefined") {
    return [];
  }

  const value = new URLSearchParams(window.location.search).get("season_id");
  const seasonId = Number(value);

  return Number.isInteger(seasonId) && seasonId > 0 ? [seasonId] : [];
}

/** Legacy callers pass no Season; a personal Season is always explicit. */
export const analyticsQueryKeys = {
  all: ["analytics"] as const,

  leagueAverage: (seasonId?: number) =>
    seasonId === undefined
      ? ([
          ...analyticsQueryKeys.all,
          "league-average",
          ...viewedSeasonKey(),
        ] as const)
      : ([...personalQueryKeys.season(seasonId), "league-average"] as const),

  optimalLineups: (seasonId?: number) =>
    seasonId === undefined
      ? ([
          ...analyticsQueryKeys.all,
          "optimal-lineups",
          ...viewedSeasonKey(),
        ] as const)
      : ([...personalQueryKeys.season(seasonId), "optimal-lineups"] as const),

  optimalLineupTeam: (teamId: number, seasonId?: number) =>
    [...analyticsQueryKeys.optimalLineups(seasonId), teamId] as const,

  scheduleSwap: (
    firstTeamId: number,
    secondTeamId: number,
    seasonId?: number,
  ) =>
    seasonId === undefined
      ? ([
          ...analyticsQueryKeys.all,
          "schedule-swap",
          firstTeamId,
          secondTeamId,
          ...viewedSeasonKey(),
        ] as const)
      : ([
          ...personalQueryKeys.season(seasonId),
          "schedule-swap",
          firstTeamId,
          secondTeamId,
        ] as const),
};

/** Season-scoped analytics path: personal when a Season is given. */
function analyticsPath(seasonId: number | undefined, suffix: string): string {
  return seasonId === undefined
    ? `/analytics/${suffix}`
    : `/seasons/${seasonId}/analytics/${suffix}`;
}

export function scheduleSwapQueryOptions(
  firstTeamId: number,
  secondTeamId: number,
  seasonId?: number,
) {
  const search = new URLSearchParams({
    first_team_id: String(firstTeamId),
    second_team_id: String(secondTeamId),
  });

  return queryOptions({
    queryKey: analyticsQueryKeys.scheduleSwap(
      firstTeamId,
      secondTeamId,
      seasonId,
    ),
    queryFn: ({ signal }) =>
      getJson<ScheduleSwapResult>(
        `${analyticsPath(seasonId, "schedule-swap")}?${search.toString()}`,
        signal,
      ),
    enabled:
      firstTeamId > 0 && secondTeamId > 0 && firstTeamId !== secondTeamId,
    staleTime: 60_000,
  });
}

export function leagueAverageQueryOptions(seasonId?: number) {
  return queryOptions({
    queryKey: analyticsQueryKeys.leagueAverage(seasonId),
    queryFn: ({ signal }) =>
      getJson<LeagueAverageStanding[]>(
        analyticsPath(seasonId, "league-average"),
        signal,
      ),
    staleTime: 60_000,
  });
}

export function optimalLineupsQueryOptions(seasonId?: number) {
  return queryOptions({
    queryKey: analyticsQueryKeys.optimalLineups(seasonId),
    queryFn: ({ signal }) =>
      getJson<TeamOptimalLineupSummary[]>(
        analyticsPath(seasonId, "optimal-lineups"),
        signal,
      ),
    staleTime: 60_000,
  });
}

export function teamOptimalLineupQueryOptions(
  teamId: number,
  seasonId?: number,
) {
  return queryOptions({
    queryKey: analyticsQueryKeys.optimalLineupTeam(teamId, seasonId),
    queryFn: ({ signal }) =>
      getJson<TeamOptimalLineupBreakdown>(
        `${analyticsPath(seasonId, "optimal-lineups")}/${teamId}`,
        signal,
      ),
    enabled: teamId > 0,
    staleTime: 60_000,
  });
}
