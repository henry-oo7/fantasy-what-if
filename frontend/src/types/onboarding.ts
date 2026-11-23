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
