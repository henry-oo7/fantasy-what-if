export interface TeamSnapshot {
  team_id: number;
  name: string;
  wins: number;
  losses: number;
  ties: number;
  points_for: number;
  points_against: number;
}

export interface MatchupSnapshot {
  week: number;
  home_team_id: number;
  away_team_id: number;
  home_score: number;
  away_score: number;
}

export interface LeagueSnapshot {
  league_id: number;
  name: string;
  season: number;
  current_week: number;
  regular_season_weeks: number;
  teams: TeamSnapshot[];
  matchups: MatchupSnapshot[];
}

export interface AllPlayStanding {
  rank: number;
  team_id: number;
  team_name: string;
  wins: number;
  losses: number;
  ties: number;
  points_for: number;
  win_percentage: number;
}

export interface ImportedSeason {
  season_id: number;
  league_id: number;
  league_name: string;
  year: number;
  status: string | null;
  is_active: boolean;
  last_synced_at: string | null;
}

export interface ImportedSeasonsResponse {
  seasons: ImportedSeason[];
}

export interface ImportSeasonRequest {
  year: number;
}

export interface ImportSeasonResponse {
  season_id: number;
  league_id: number;
  year: number;
  status: string;
  synchronization_enabled: boolean;
  teams_upserted: number;
  matchups_upserted: number;
  fantasy_byes_upserted: number;
  player_week_scores_upserted: number;
}
