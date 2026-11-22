export type UserRole = "spectator" | "admin";

/** GET /api/auth/me */
export interface AuthenticatedUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  role: UserRole;
  is_active: boolean;
}

export interface CsrfTokenResponse {
  csrf_token: string;
}

export interface LogoutResponse {
  logged_out: boolean;
}

/**
 * POST /api/auth/login: legacy username compatibility.
 *
 * Used only by the legacy single-installation setup bootstrap. New SaaS
 * sign-in uses the administrator email and spectator workspace routes.
 */
export interface LegacyUsernameLoginRequest {
  username: string;
  password: string;
}

export interface LegacyAuthenticationResponse {
  user: AuthenticatedUser;
  csrf_token: string;
}

/** POST /api/auth/admin/login */
export interface AdministratorLoginRequest {
  email: string;
  password: string;
}

export interface AdministratorAuthenticatedUser {
  id: number;
  workspace_id: number;
  display_name: string;
  email: string;
  role: "admin";
  is_active: boolean;
}

export interface AdministratorAuthenticationResponse {
  user: AdministratorAuthenticatedUser;
  csrf_token: string;
}

/** POST /api/auth/spectator/login */
export interface SpectatorLoginRequest {
  workspace_slug: string;
  password: string;
}

export interface SpectatorAuthenticatedUser {
  id: number;
  workspace_id: number;
  workspace_slug: string;
  display_name: string;
  role: "spectator";
  is_active: boolean;
}

export interface SpectatorAuthenticationResponse {
  user: SpectatorAuthenticatedUser;
  csrf_token: string;
}

/** POST /api/auth/password-reset/request */
export interface PasswordResetRequest {
  email: string;
}

export interface PasswordResetRequestResponse {
  detail: string;
  code: string;
}

/** POST /api/auth/password-reset/complete */
export interface PasswordResetCompletionRequest {
  token: string;
  new_password: string;
}

export interface PasswordResetCompletionResponse {
  detail: string;
  code: string;
  revoked_sessions: number;
}

/**
 * Workspace lifecycle as observed through an active-workspace route.
 *
 * - active: ordinary application access
 * - provisional: administrator still in SaaS onboarding
 * - suspended: workspace_suspended
 * - inactive: workspace_inactive for a non-onboarding user
 * - checking: not yet determined
 */
export type WorkspaceAccess =
  | "checking"
  | "active"
  | "provisional"
  | "suspended"
  | "inactive";

/** Messages shown on the login page after a lifecycle event. */
export type AuthenticationNotice =
  | "session_replaced"
  | "password_reset_completed";
