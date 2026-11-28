/** Personal League and Season contracts (ADR 0010). `season_id` is a locator, never authorization. */

export type SeasonAccessStatus = "active" | "reverification_required";

export type SeasonSyncState =
  | "never_synced"
  | "queued"
  | "syncing"
  | "fresh"
  | "stale"
  | "failed";

export interface PersonalSeason {
  season_id: number;
  season_year: number;
  access_status: SeasonAccessStatus;
  /** Null only for a legacy Season never described by the sync lifecycle. */
  sync_state: SeasonSyncState | null;
  data_available: boolean;
  last_successful_sync_at: string | null;
}

export interface PersonalLeague {
  provider: "espn";
  provider_league_id: string;
  league_name: string;
  seasons: PersonalSeason[];
}

export interface PersonalLeagues {
  default_season_id: number | null;
  leagues: PersonalLeague[];
}

export interface PersonalSeasonDetail extends PersonalSeason {
  provider: "espn";
  provider_league_id: string;
  league_name: string;
}

export interface EspnPrivateCredentialsRequest {
  swid: string;
  espn_s2: string;
}

export interface LeagueImportRequest {
  provider: "espn";
  provider_league_id: string;
  season_year: number;
  credentials?: EspnPrivateCredentialsRequest;
}

export interface LeagueImportResponse {
  provider: "espn";
  provider_league_id: string;
  league_name: string;
  season_id: number;
  season_year: number;
  access_status: "active";
  sync_state: SeasonSyncState;
  data_available: boolean;
  sync_queued: boolean;
}

export interface DefaultSeasonResponse {
  default_season_id: number;
}
