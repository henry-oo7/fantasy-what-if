export type UserRole = "spectator" | "admin";

export interface AuthenticatedUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  role: UserRole;
  is_active: boolean;
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

export interface LogoutResponse {
  logged_out: boolean;
}
