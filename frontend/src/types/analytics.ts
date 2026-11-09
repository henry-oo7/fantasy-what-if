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


export interface LeagueAverageWeek {
  week: number;
  team_score: number;
  league_average: number;
  actual_opponent_id: number;
  actual_opponent_name: string;
  actual_opponent_score: number;
  actual_result: MatchupResult;
  average_result: MatchupResult;
}

export interface LeagueAverageStanding {
  rank: number;
  team_id: number;
  team_name: string;
  actual_record: AnalyticsRecord;
  average_record: AnalyticsRecord;
  combined_record: AnalyticsRecord;
  points_for: number;
  average_points_per_week: number;
  weeks_played: number;
  weekly_results: LeagueAverageWeek[];
}


export interface LineupPlayer {
  lineup_slot: string;
  player_id: number | null;
  player_name: string | null;
  default_position: string | null;
  pro_team: string | null;
  points: number;
  actual_lineup_slot: string | null;
  was_actual_starter: boolean;
  is_unfilled: boolean;
}

export interface LineupSubstitution {
  lineup_slot: string;
  player_in_id: number | null;
  player_in_name: string | null;
  player_out_id: number | null;
  player_out_name: string | null;
  point_change: number;
}

export interface TeamWeekOptimalLineup {
  team_id: number;
  team_name: string;
  week: number;
  actual_points: number;
  optimal_points: number;
  missed_points: number;
  efficiency_percentage: number;
  actual_starter_count: number;
  optimal_filled_slot_count: number;
  unfilled_slots: string[];
  actual_lineup: LineupPlayer[];
  optimal_lineup: LineupPlayer[];
  substitutions: LineupSubstitution[];
}

export interface TeamOptimalLineupSummary {
  team_id: number;
  team_name: string;
  weeks_played: number;
  actual_points: number;
  optimal_points: number;
  missed_points: number;
  efficiency_percentage: number;
  weekly_results: TeamWeekOptimalLineup[];
}



export interface TeamWeekOptimalLineupBreakdown
  extends TeamWeekOptimalLineup {
  opponent_team_id: number;
  opponent_team_name: string;
  opponent_points: number;
  actual_result: MatchupResult;
  optimal_result: MatchupResult;
  result_flipped: boolean;
}

export interface TeamOptimalLineupBreakdown {
  team_id: number;
  team_name: string;
  weeks_played: number;
  actual_points: number;
  optimal_points: number;
  missed_points: number;
  efficiency_percentage: number;
  actual_record: AnalyticsRecord;
  optimal_record: AnalyticsRecord;
  equivalent_wins_gained: number;
  results_flipped: number;
  weekly_results: TeamWeekOptimalLineupBreakdown[];
}
