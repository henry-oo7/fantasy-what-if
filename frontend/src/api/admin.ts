import {
  getJson,
  patchJson,
  postJson,
} from "./client";
import type {
  CreateSpectatorRequest,
  SpectatorAccountResponse,
} from "../types/setup";


export function getSpectatorAccount(
  signal?: AbortSignal,
): Promise<SpectatorAccountResponse> {
  return getJson<SpectatorAccountResponse>(
    "/admin/spectator",
    signal,
  );
}


export function createSpectatorAccount(
  request: CreateSpectatorRequest,
  csrfToken: string,
): Promise<SpectatorAccountResponse> {
  return postJson<SpectatorAccountResponse>(
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
): Promise<SpectatorAccountResponse> {
  return postJson<SpectatorAccountResponse>(
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
): Promise<SpectatorAccountResponse> {
  return patchJson<SpectatorAccountResponse>(
    "/admin/spectator/access",
    {
      is_active: isActive,
    },
    {
      csrfToken,
    },
  );
}
