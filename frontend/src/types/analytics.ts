export type MatchupResult =
  | "win"
  | "loss"
  | "tie";

export interface AnalyticsRecord {
  wins: number;
  losses: number;
  ties: number;
  win_percentage: number;
}

export interface ScheduleSwapWeek {
  week: number;
  team_id: number;
  team_name: string;
  borrowed_schedule_team_id: number;
  borrowed_schedule_team_name: string;
  hypothetical_opponent_id: number;
  hypothetical_opponent_name: string;
  team_score: number;
  opponent_score: number;
  result: MatchupResult;
}

export interface TeamScheduleSwapResult {
  team_id: number;
  team_name: string;
  borrowed_schedule_team_id: number;
  borrowed_schedule_team_name: string;
  original_record: AnalyticsRecord;
  swapped_record: AnalyticsRecord;
  equivalent_wins_change: number;
  weekly_results: ScheduleSwapWeek[];
}

export interface ScheduleSwapResult {
  first_team: TeamScheduleSwapResult;
  second_team: TeamScheduleSwapResult;
  completed_weeks: number[];
}
