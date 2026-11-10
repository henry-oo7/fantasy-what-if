import type { ImportedSeason, LeagueSnapshot } from "../types/league";

export interface SeasonPresentation {
  statusLabel: string;
  pageKicker: string;
  periodLabel: string;
  periodValue: string;
  periodDetail: string;
  completedWeeks: number;
  isActive: boolean;
}

export function completedWeekCount(league: LeagueSnapshot): number {
  const latestStoredWeek = league.matchups.reduce(
    (latest, matchup) => Math.max(latest, matchup.week),
    0,
  );

  return Math.min(latestStoredWeek, league.regular_season_weeks);
}

export function seasonPresentation(
  league: LeagueSnapshot,
  season: ImportedSeason | undefined,
): SeasonPresentation {
  const completedWeeks = completedWeekCount(league);
  const status = season?.status;
  const isActive = status === "active" && season?.is_active === true;

  if (status === "completed") {
    return {
      statusLabel: "Season completed",
      pageKicker: "Season completed",
      periodLabel: "Season",
      periodValue: "Completed",
      periodDetail:
        `${completedWeeks} of ` +
        `${league.regular_season_weeks} ` +
        "regular-season weeks complete",
      completedWeeks,
      isActive: false,
    };
  }

  if (status === "archived") {
    return {
      statusLabel: "Season archived",
      pageKicker: "Season archived",
      periodLabel: "Season",
      periodValue: "Archived",
      periodDetail:
        `${completedWeeks} completed regular-season ` +
        `${completedWeeks === 1 ? "week" : "weeks"} stored`,
      completedWeeks,
      isActive: false,
    };
  }

  if (status === "draft") {
    return {
      statusLabel: "Season draft",
      pageKicker: "Season inactive",
      periodLabel: "Season status",
      periodValue: "Season inactive",
      periodDetail: "Synchronization is not active",
      completedWeeks,
      isActive: false,
    };
  }

  if (isActive) {
    return {
      statusLabel: "Season active",
      pageKicker: `Week ${league.current_week}`,
      periodLabel: "Current week",
      periodValue: `Week ${league.current_week}`,
      periodDetail: `Through week ${completedWeeks} results included`,
      completedWeeks,
      isActive: true,
    };
  }

  return {
    statusLabel: "Season imported",
    pageKicker: "Season inactive",
    periodLabel: "Season status",
    periodValue: "Season inactive",
    periodDetail:
      completedWeeks > 0
        ? `Through week ${completedWeeks} results included`
        : "No completed weekly results",
    completedWeeks,
    isActive: false,
  };
}
