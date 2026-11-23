import { getJson, postJson } from "./client";
import type {
  InitialSyncProgress,
  OnboardingSpectatorState,
  ProviderClaimResponse,
  ProviderTarget,
  ProviderValidationRequest,
  ProviderValidationResponse,
} from "../types/onboarding";

export const onboardingQueryKeys = {
  all: ["onboarding"] as const,
  providerTarget: () => [...onboardingQueryKeys.all, "provider-target"] as const,
  synchronization: () => [...onboardingQueryKeys.all, "synchronization"] as const,
  spectator: () => [...onboardingQueryKeys.all, "spectator"] as const,
};

/**
 * Check that Fantasy What-If can read one ESPN league season.
 *
 * Deliberately not a React Query mutation: the mutation cache would retain
 * submitted credentials as variables. The body travels only in this POST.
 */
export function validateProviderAccess(
  request: ProviderValidationRequest,
  csrfToken: string | null,
): Promise<ProviderValidationResponse> {
  return postJson<ProviderValidationResponse>(
    "/onboarding/provider/validate",
    request,
    { csrfToken },
  );
}

export function getProviderTarget(signal?: AbortSignal): Promise<ProviderTarget> {
  return getJson<ProviderTarget>("/onboarding/provider/target", signal);
}

/** Confirm the validated target. The body carries no ownership fields. */
export function claimProviderLeague(
  csrfToken: string | null,
): Promise<ProviderClaimResponse> {
  return postJson<ProviderClaimResponse>("/onboarding/provider/claim", {}, { csrfToken });
}

export function getInitialSyncProgress(signal?: AbortSignal): Promise<InitialSyncProgress> {
  return getJson<InitialSyncProgress>("/onboarding/synchronization", signal);
}

/** Queue (or return the active) first import. The body carries no ownership data. */
export function startInitialSync(csrfToken: string | null): Promise<InitialSyncProgress> {
  return postJson<InitialSyncProgress>("/onboarding/synchronization", {}, { csrfToken });
}

export function getOnboardingSpectator(signal?: AbortSignal): Promise<OnboardingSpectatorState> {
  return getJson<OnboardingSpectatorState>("/onboarding/spectator", signal);
}

/**
 * Create the one shared spectator. Not a React Query mutation, so the
 * password is never retained in a mutation cache.
 */
export function setUpOnboardingSpectator(
  password: string,
  csrfToken: string | null,
): Promise<OnboardingSpectatorState> {
  return postJson<OnboardingSpectatorState>("/onboarding/spectator", { password }, { csrfToken });
}
