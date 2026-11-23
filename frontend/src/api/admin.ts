import {
  queryOptions,
} from "@tanstack/react-query";

import {
  getJson,
  patchJson,
  postJson,
} from "./client";
import type {
  CreateSpectatorRequest,
  SpectatorAccount,
  StandingsTiebreakerConfiguration,
  StandingsTiebreakerRule,
  BackgroundJob,
  ReplaceProviderCredentialsRequest,
  ReplaceProviderCredentialsResponse,
  SyncJobAccepted,
  SyncStatus,
} from "../types/admin";


export const adminQueryKeys = {
  all: ["admin"] as const,

  spectator: () =>
    [
      ...adminQueryKeys.all,
      "spectator",
    ] as const,

  syncStatus: () =>
    [
      ...adminQueryKeys.all,
      "sync-status",
    ] as const,

  job: (jobId: number) =>
    [
      ...adminQueryKeys.all,
      "jobs",
      jobId,
    ] as const,

  standingsTiebreaker: () =>
    [
      ...adminQueryKeys.all,
      "standings-tiebreaker",
    ] as const,
};


export function spectatorQueryOptions() {
  return queryOptions({
    queryKey: adminQueryKeys.spectator(),
    queryFn: ({ signal }) =>
      getJson<SpectatorAccount>(
        "/admin/spectator",
        signal,
      ),
  });
}


export function getSynchronizationStatus(
  signal?: AbortSignal,
): Promise<SyncStatus> {
  return getJson<SyncStatus>(
    "/admin/sync-status",
    signal,
  );
}


export function syncStatusQueryOptions() {
  return queryOptions({
    queryKey: adminQueryKeys.syncStatus(),
    queryFn: ({ signal }) =>
      getSynchronizationStatus(signal),
    staleTime: 30_000,
  });
}


export function standingsTiebreakerQueryOptions() {
  return queryOptions({
    queryKey: adminQueryKeys.standingsTiebreaker(),
    queryFn: ({ signal }) =>
      getJson<StandingsTiebreakerConfiguration>(
        "/admin/standings-tiebreaker",
        signal,
      ),
  });
}


export function setStandingsTiebreakerFallback(
  fallbackRule: StandingsTiebreakerRule,
  csrfToken: string,
): Promise<StandingsTiebreakerConfiguration> {
  return patchJson<StandingsTiebreakerConfiguration>(
    "/admin/standings-tiebreaker",
    {
      fallback_rule: fallbackRule,
    },
    {
      csrfToken,
    },
  );
}


/** Shared spectator password policy (ADR 0005). Values are never trimmed. */
export const SPECTATOR_PASSWORD_MIN_LENGTH = 14;
export const SPECTATOR_PASSWORD_MAX_LENGTH = 128;

export function createSpectatorAccount(
  request: CreateSpectatorRequest,
  csrfToken: string,
): Promise<SpectatorAccount> {
  return postJson<SpectatorAccount>(
    "/admin/spectator",
    request,
    {
      csrfToken,
    },
  );
}


export function resetSpectatorPassword(
  password: string,
  csrfToken: string,
): Promise<SpectatorAccount> {
  return postJson<SpectatorAccount>(
    "/admin/spectator/reset-password",
    {
      password,
    },
    {
      csrfToken,
    },
  );
}


/** Disable access, or re-enable it with a required new shared password. */
export function setSpectatorAccess(
  isActive: boolean,
  csrfToken: string,
  password?: string,
): Promise<SpectatorAccount> {
  return patchJson<SpectatorAccount>(
    "/admin/spectator/access",
    isActive ? { is_active: true, password } : { is_active: false },
    {
      csrfToken,
    },
  );
}


export function runSynchronization(
  fullSync: boolean,
  csrfToken: string,
): Promise<SyncJobAccepted> {
  return postJson<SyncJobAccepted>(
    "/admin/sync",
    {
      full_sync: fullSync,
    },
    {
      csrfToken,
    },
  );
}


export function getBackgroundJob(
  jobId: number,
  signal?: AbortSignal,
): Promise<BackgroundJob> {
  return getJson<BackgroundJob>(
    `/admin/jobs/${jobId}`,
    signal,
  );
}


export interface PollBackgroundJobOptions {
  intervalMs?: number;
  timeoutMs?: number;
  maximumConsecutiveReadFailures?: number;
}

const TERMINAL_JOB_STATUSES = new Set([
  "succeeded",
  "failed",
  "canceled",
]);

export async function pollBackgroundJob(
  jobId: number,
  {
    intervalMs = 2_000,
    timeoutMs = 300_000,
    maximumConsecutiveReadFailures = 5,
  }: PollBackgroundJobOptions = {},
): Promise<BackgroundJob> {
  const startedAt = Date.now();
  let consecutiveReadFailures = 0;

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const job = await getBackgroundJob(jobId);

      consecutiveReadFailures = 0;

      if (TERMINAL_JOB_STATUSES.has(job.status)) {
        return job;
      }
    } catch (error) {
      consecutiveReadFailures += 1;

      if (
        consecutiveReadFailures >=
        maximumConsecutiveReadFailures
      ) {
        throw error;
      }
    }

    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, intervalMs);
    });
  }

  throw new Error(
    "Synchronization is still running. Refresh the page to check its persisted job status.",
  );
}

export function replaceProviderCredentials(
  request: ReplaceProviderCredentialsRequest,
  csrfToken: string,
): Promise<ReplaceProviderCredentialsResponse> {
  return patchJson<ReplaceProviderCredentialsResponse>(
    "/admin/provider-credentials",
    request,
    {
      csrfToken,
    },
  );
}
