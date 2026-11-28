import type { NavigationState } from "./auth";

/** POST /api/registration */
export interface RegistrationRequest {
  display_name: string;
  email: string;
  password: string;
  confirm_password: string;
}

export interface RegistrationResponse {
  verification_required: true;
}

/** POST /api/registration/verify-email */
export interface EmailVerificationRequest {
  token: string;
}

export interface EmailVerificationResponse {
  display_name: string;
  email: string;
  email_verified: true;
  navigation_state: NavigationState;
  csrf_token: string;
}

/** POST /api/registration/resend-verification */
export interface VerificationResendRequest {
  email: string;
}

export interface VerificationResendResponse {
  detail: string;
  code: "verification_resend_accepted";
}
