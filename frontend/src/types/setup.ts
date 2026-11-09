export type InstallationStatus =
  | "setup_required"
  | "setup_in_progress"
  | "active";

export interface SetupStatus {
  status: InstallationStatus;
  installation_exists: boolean;
  bootstrap_available: boolean;
  administrator_configured: boolean;
  provider_configured: boolean;
  spectator_configured: boolean;
  active_league_configured: boolean;
}

export interface BootstrapAdminRequest {
  username: string;
  display_name: string;
  email: string | null;
  password: string;
}

export interface BootstrapAdminResponse {
  installation_status: "setup_in_progress";
  administrator_id: number;
  username: string;
  display_name: string;
  email: string | null;
}

export type FantasyProvider =
  | "espn"
  | "sleeper"
  | "yahoo";

export interface SetupProvider {
  provider: FantasyProvider;
  display_name: string;
  implemented: boolean;
  requires_credentials: boolean;
  credential_fields: string[];
}

export interface SetupProvidersResponse {
  providers: SetupProvider[];
}

export interface ProviderConnectionRequest {
  provider: FantasyProvider;
  display_name: string;
  league_id: string;
  season: string;
  espn_s2: string | null;
  swid: string | null;
}

export interface ProviderConnectionResponse {
  connection_id: number;
  provider: FantasyProvider;
  display_name: string;
  credential_version: number;
  is_active: boolean;
  validated: boolean;
}

export interface SetupFinalizationResponse {
  installation_status: "active";
  installation_id: number;
  league_id: number;
  season_id: number;
  provider: FantasyProvider;
  provider_league_id: string;
  provider_season_id: string;
  current_week: number;
  full_sync: boolean;
  teams_upserted: number;
  matchups_upserted: number;
  fantasy_byes_upserted: number;
  player_week_scores_upserted: number;
}


export interface CreateSpectatorRequest {
  username: string;
  display_name: "League Spectator";
  email: null;
  password: string;
}

export interface SpectatorAccountResponse {
  configured: boolean;
  id: number | null;
  username: string | null;
  display_name: string | null;
  email: string | null;
  is_active: boolean | null;
  revoked_sessions?: number;
}

