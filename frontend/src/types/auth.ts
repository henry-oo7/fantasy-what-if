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

/**
 * What the current session may do. A deletion_recovery session (FR-20)
 * may only read /auth/me and deletion status, cancel deletion, and log out.
 */
export type SessionPurpose = "standard" | "deletion_recovery";

/** Where the server says this account belongs next (GET /api/auth/me). */
export type NavigationState =
  | "email_verification_required"
  | "import_required"
  | "personal_dashboard"
  | "legacy_workspace"
  | "legacy_deletion_recovery";

/** Legacy workspace compatibility; null for a personal account. */
export interface LegacyWorkspaceContext {
  role: UserRole;
  workspace: WorkspaceLifecycle;
}

/** GET /api/auth/me: the personal account and its navigation state. */
export interface AuthenticatedUser {
  authenticated: true;
  display_name: string;
  email: string | null;
  email_verified: boolean;
  navigation_state: NavigationState;
  legacy_workspace: LegacyWorkspaceContext | null;
  /** Absent on older responses; treated as standard. */
  session_purpose?: SessionPurpose;
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
  display_name: string;
  email: string;
  email_verified: boolean;
  is_active: boolean;
  /** Legacy workspace administrators only. */
  workspace_id?: number;
  role?: "admin";
}

export interface AdministratorAuthenticationResponse {
  user: AdministratorAuthenticatedUser;
  csrf_token: string;
  session_purpose?: SessionPurpose;
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
 * - personal: a workspace-free personal account (no workspace to gate)
 * - deletion_recovery: restricted administrator session for a workspace
 *   scheduled for deletion; only the recovery screen is reachable
 * - checking: no session has been resolved yet
 */
export type RoutedWorkspaceAccess =
  | ResolvedWorkspaceAccess
  | "deletion_recovery"
  | "personal";

export type WorkspaceAccess =
  | "checking"
  | RoutedWorkspaceAccess;

/** Messages shown on the login page after a lifecycle event. */
export type AuthenticationNotice =
  | "session_replaced"
  | "password_reset_completed"
  | "league_already_claimed"
  | "workspace_deletion_canceled"
  | "account_deleted";
