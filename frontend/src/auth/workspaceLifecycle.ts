import type {
  AuthenticatedUser,
  OnboardingStage,
  RoutedWorkspaceAccess,
} from "../types/auth";

/**
 * Routing access from the authoritative GET /api/auth/me workspace object.
 *
 * Unknown or missing values are treated as inactive, and only an
 * administrator can ever be routed to provisional onboarding. A
 * deletion_recovery session is routed only to the recovery screen,
 * whatever the workspace object says.
 */
export function workspaceAccessFor(
  user: AuthenticatedUser,
): RoutedWorkspaceAccess {
  const purpose: unknown = user.session_purpose;

  if (purpose === "deletion_recovery") {
    return user.role === "admin" ? "deletion_recovery" : "inactive";
  }

  const access: unknown = user.workspace?.workspace_access;

  switch (access) {
    case "active":
    case "suspended":
    case "inactive":
      return access;
    case "provisional":
      return user.role === "admin" ? "provisional" : "inactive";
    default:
      return "inactive";
  }
}

const ONBOARDING_STAGE_MESSAGES: Record<OnboardingStage, string> = {
  registration_pending: "Your registration is being prepared.",
  email_verification_pending: "Your email address is waiting to be verified.",
  provider_setup_pending: "Next step: connect your fantasy provider.",
  league_claim_pending: "Next step: confirm and claim your ESPN league.",
  initial_sync_pending: "Next step: load your league data.",
  initial_sync_running: "Your league data is loading.",
  spectator_setup_pending: "Next step: set up spectator access.",
  complete: "Workspace setup is complete.",
  provider_validation_failed:
    "Your fantasy provider connection needs attention.",
  league_claim_conflict:
    "The league you chose needs attention before setup can continue.",
  initial_sync_failed: "Loading your league data did not finish.",
};

/** Safe user-facing copy for an onboarding stage; never the raw value. */
export function onboardingStageMessage(stage: unknown): string | null {
  if (typeof stage !== "string" || !Object.hasOwn(ONBOARDING_STAGE_MESSAGES, stage)) {
    return null;
  }

  return ONBOARDING_STAGE_MESSAGES[stage as OnboardingStage];
}
