import type { OnboardingStage } from "./auth";

/** ESPN private-league cookies. Held only in form state until submission. */
export interface EspnPrivateCredentials {
  swid: string;
  espn_s2: string;
}

/**
 * POST /api/onboarding/provider/validate
 *
 * league_id stays an opaque decimal string end to end; it is never
 * converted to a JavaScript number.
 */
export interface ProviderValidationRequest {
  provider: "espn";
  league_id: string;
  season_year: number;
  credentials?: EspnPrivateCredentials;
}

export interface ProviderValidationResponse {
  status: "validated" | "credentials_required";
  code: "provider_validated" | "credentials_required";
  provider: "espn";
  access_mode: "public" | "private" | null;
  onboarding_stage: OnboardingStage;
}

/** GET /api/onboarding/provider/target: safe target, never credentials. */
export interface ProviderTarget {
  provider: "espn";
  league_id: string;
  season_year: number;
  access_mode: "public" | "private";
}

/** POST /api/onboarding/provider/claim */
export interface ProviderClaimResponse {
  status: "claimed";
  code: "provider_league_claimed";
  onboarding_stage: "initial_sync_pending";
  target: ProviderTarget;
}

export type InitialSyncJobStatus =
  | "pending"
  | "running"
  | "retry_scheduled"
  | "succeeded"
  | "failed"
  | "canceled";

/** Safe job fields only. No payload, lease, worker or raw error data. */
export interface InitialSyncJob {
  id: number;
  status: InitialSyncJobStatus;
  retryable: boolean;
  detail: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
}

/** GET and POST /api/onboarding/synchronization */
export interface InitialSyncProgress {
  onboarding_stage: OnboardingStage;
  job: InitialSyncJob | null;
}
