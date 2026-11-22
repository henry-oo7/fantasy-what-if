/** POST /api/registration */
export interface AdministratorRegistrationRequest {
  workspace_name: string;
  workspace_slug: string;
  display_name: string;
  email: string;
  password: string;
}

export interface AdministratorRegistrationResponse {
  workspace_slug: string;
  registration_status: "provisional";
  onboarding_stage: "email_verification_pending";
  verification_required: true;
}

/** POST /api/registration/verify-email */
export interface EmailVerificationRequest {
  token: string;
}

export interface EmailVerificationResponse {
  workspace_slug: string;
  display_name: string;
  email: string;
  role: "admin";
  registration_status: "provisional";
  onboarding_stage: "provider_setup_pending";
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
