export type UserRole = "spectator" | "admin";

export type InstallationStatus =
  | "setup_required"
  | "setup_in_progress"
  | "active";

export type WorkspaceRegistrationStatus =
  | "provisional"
  | "active"
  | "suspended"
  | "deletion_pending"
  | "deleted";

export type OnboardingStage =
  | "registration_pending"
  | "email_verification_pending"
  | "provider_setup_pending"
  | "league_claim_pending"
  | "initial_sync_pending"
  | "initial_sync_running"
  | "spectator_setup_pending"
  | "complete"
  | "provider_validation_failed"
  | "league_claim_conflict"
  | "initial_sync_failed";

/** Authoritative workspace access returned by GET /api/auth/me. */
export type ResolvedWorkspaceAccess =
  | "active"
  | "provisional"
  | "suspended"
  | "inactive";

/**
 * The signed-in user's own workspace lifecycle.
 *
 * Lifecycle detail is null when access is inactive, and onboarding_stage
 * is returned only to administrators.
 */
export interface WorkspaceLifecycle {
  workspace_id: number;
  workspace_slug: string | null;
  installation_status: InstallationStatus | null;
  registration_status: WorkspaceRegistrationStatus | null;
  onboarding_stage: OnboardingStage | null;
  workspace_access: ResolvedWorkspaceAccess;
}

/** GET /api/auth/me: identity plus authoritative workspace lifecycle. */
export interface AuthenticatedUser {
  id: number;
  display_name: string;
  email: string | null;
  role: UserRole;
  is_active: boolean;
  workspace: WorkspaceLifecycle;
}

export interface CsrfTokenResponse {
  csrf_token: string;
}

export interface LogoutResponse {
  logged_out: boolean;
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
 * Workspace access used for routing.
 *
 * - active: ordinary application access
 * - provisional: administrator still in SaaS onboarding
 * - suspended: the workspace is suspended
 * - inactive: any other unusable workspace, or an unknown value
 * - checking: no session has been resolved yet
 */
export type WorkspaceAccess =
  | "checking"
  | ResolvedWorkspaceAccess;

/** Messages shown on the login page after a lifecycle event. */
export type AuthenticationNotice =
  | "session_replaced"
  | "password_reset_completed"
  | "league_already_claimed";
