export type UserRole = "member" | "admin";

export interface AuthenticatedUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  role: UserRole;
  is_active: boolean;
  team_id: number | null;
  team_name: string | null;
  season: number | null;
}

export interface AuthenticationResponse {
  user: AuthenticatedUser;
  csrf_token: string;
}

export interface CsrfTokenResponse {
  csrf_token: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RegistrationRequest {
  invitation_token: string;
  username: string;
  display_name: string;
  email: string | null;
  password: string;
}

export interface InvitationValidation {
  valid: boolean;
  team_id: number;
  team_name: string;
  season: number;
  intended_display_name: string | null;
  expires_at: string;
}

export interface LogoutResponse {
  logged_out: boolean;
}
