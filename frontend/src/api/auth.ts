import {
  getJson,
  postJson,
} from "./client";
import type {
  AuthenticatedUser,
  AuthenticationResponse,
  CsrfTokenResponse,
  InvitationValidation,
  LoginRequest,
  LogoutResponse,
  RegistrationRequest,
} from "../types/auth";

export function requestPreAuthCsrf(
  signal?: AbortSignal,
): Promise<CsrfTokenResponse> {
  return getJson<CsrfTokenResponse>(
    "/auth/csrf",
    signal,
  );
}

export function getCurrentUser(
  signal?: AbortSignal,
): Promise<AuthenticatedUser> {
  return getJson<AuthenticatedUser>(
    "/auth/me",
    signal,
  );
}

export function validateInvitation(
  token: string,
  signal?: AbortSignal,
): Promise<InvitationValidation> {
  return getJson<InvitationValidation>(
    `/auth/invitations/${encodeURIComponent(token)}`,
    signal,
  );
}

export function login(
  request: LoginRequest,
  csrfToken: string,
): Promise<AuthenticationResponse> {
  return postJson<AuthenticationResponse>(
    "/auth/login",
    request,
    {
      csrfToken,
    },
  );
}

export function register(
  request: RegistrationRequest,
  csrfToken: string,
): Promise<AuthenticationResponse> {
  return postJson<AuthenticationResponse>(
    "/auth/register",
    request,
    {
      csrfToken,
    },
  );
}

export function logout(
  csrfToken: string,
): Promise<LogoutResponse> {
  return postJson<LogoutResponse>(
    "/auth/logout",
    {},
    {
      csrfToken,
    },
  );
}
