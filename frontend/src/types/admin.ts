export interface SpectatorAccount {
  configured: boolean;
  id: number | null;
  username: string | null;
  display_name: string | null;
  email: string | null;
  is_active: boolean | null;
  revoked_sessions?: number;
}

export interface SyncRun {
  id: number;
  status: string;
  is_full_sync: boolean;
  started_at: string;
  completed_at: string | null;
  requested_from_week: number | null;
  requested_through_week: number | null;
  teams_upserted: number;
  matchups_upserted: number;
  player_week_scores_upserted: number;
  safe_error_message: string | null;
}

export interface SyncStatus {
  provider: string;
  season_id: number;
  provider_league_id: string;
  provider_season_id: string;
  last_synced_at: string | null;
  latest_run: SyncRun | null;
}

export interface SyncResult {
  provider: string;
  season_id: number;
  provider_league_id: string;
  provider_season_id: string;
  current_week: number;
  full_sync: boolean;
  player_weeks: number[];
  teams_upserted: number;
  matchups_upserted: number;
  fantasy_byes_upserted: number;
  player_week_scores_upserted: number;
}
