import {
  getJson,
  postJson,
} from "./client";
import type {
  AdministratorAuthenticationResponse,
  AdministratorLoginRequest,
  AuthenticatedUser,
  CsrfTokenResponse,
  LegacyAuthenticationResponse,
  LegacyUsernameLoginRequest,
  LogoutResponse,
  PasswordResetCompletionRequest,
  PasswordResetCompletionResponse,
  PasswordResetRequest,
  PasswordResetRequestResponse,
  SpectatorAuthenticationResponse,
  SpectatorLoginRequest,
} from "../types/auth";
import type {
  AdministratorRegistrationRequest,
  AdministratorRegistrationResponse,
  EmailVerificationResponse,
  VerificationResendResponse,
} from "../types/registration";

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

/**
 * POST a public account request with a fresh pre-auth CSRF token.
 *
 * Public account endpoints never report errors to global listeners:
 * their 401/403/409/429 responses are expected form outcomes, not
 * changes to the current session.
 */
async function postPublic<T>(
  path: string,
  body: unknown,
): Promise<T> {
  const csrf = await requestPreAuthCsrf();

  return postJson<T>(path, body, {
    csrfToken: csrf.csrf_token,
    reportErrors: false,
  });
}

export function loginAdministrator(
  request: AdministratorLoginRequest,
): Promise<AdministratorAuthenticationResponse> {
  return postPublic<AdministratorAuthenticationResponse>(
    "/auth/admin/login",
    request,
  );
}

export function loginSpectator(
  request: SpectatorLoginRequest,
): Promise<SpectatorAuthenticationResponse> {
  return postPublic<SpectatorAuthenticationResponse>(
    "/auth/spectator/login",
    request,
  );
}

/**
 * Legacy username login, kept only for the single-installation setup
 * bootstrap. New SaaS sign-in must not call this.
 */
export function loginLegacyUsername(
  request: LegacyUsernameLoginRequest,
): Promise<LegacyAuthenticationResponse> {
  return postPublic<LegacyAuthenticationResponse>(
    "/auth/login",
    request,
  );
}

export function registerAdministrator(
  request: AdministratorRegistrationRequest,
): Promise<AdministratorRegistrationResponse> {
  return postPublic<AdministratorRegistrationResponse>(
    "/registration",
    request,
  );
}

export function verifyEmail(
  token: string,
): Promise<EmailVerificationResponse> {
  return postPublic<EmailVerificationResponse>(
    "/registration/verify-email",
    { token },
  );
}

export function resendVerification(
  email: string,
): Promise<VerificationResendResponse> {
  return postPublic<VerificationResendResponse>(
    "/registration/resend-verification",
    { email },
  );
}

export function requestPasswordReset(
  request: PasswordResetRequest,
): Promise<PasswordResetRequestResponse> {
  return postPublic<PasswordResetRequestResponse>(
    "/auth/password-reset/request",
    request,
  );
}

export function completePasswordReset(
  request: PasswordResetCompletionRequest,
): Promise<PasswordResetCompletionResponse> {
  return postPublic<PasswordResetCompletionResponse>(
    "/auth/password-reset/complete",
    request,
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
      reportErrors: false,
    },
  );
}
