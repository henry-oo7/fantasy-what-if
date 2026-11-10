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
} from "../types/setup";
import type {
  SpectatorAccount,
  SyncResult,
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


export function syncStatusQueryOptions() {
  return queryOptions({
    queryKey: adminQueryKeys.syncStatus(),
    queryFn: ({ signal }) =>
      getJson<SyncStatus>(
        "/admin/sync-status",
        signal,
      ),
    staleTime: 30_000,
  });
}


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


export function setSpectatorAccess(
  isActive: boolean,
  csrfToken: string,
): Promise<SpectatorAccount> {
  return patchJson<SpectatorAccount>(
    "/admin/spectator/access",
    {
      is_active: isActive,
    },
    {
      csrfToken,
    },
  );
}


export function runSynchronization(
  fullSync: boolean,
  csrfToken: string,
): Promise<SyncResult> {
  return postJson<SyncResult>(
    "/admin/sync",
    {
      full_sync: fullSync,
    },
    {
      csrfToken,
    },
  );
}
