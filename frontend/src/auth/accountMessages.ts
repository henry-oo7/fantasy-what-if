import { ApiError } from "../api/client";

export const SESSION_REPLACED_MESSAGE =
  "This administrator account was signed in from another device. Sign in again to continue.";

export const PASSWORD_RESET_COMPLETED_MESSAGE =
  "Your password has been reset. Sign in with your administrator email.";

export const WORKSPACE_DELETION_CANCELED_MESSAGE =
  "Deletion canceled. Sign in again to continue.";

export const ACCOUNT_DELETED_MESSAGE =
  "Your account was deleted and you were signed out. League data shared with other accounts may remain for a limited time. Register again to start over.";

export const WORKSPACE_NOT_RESTORABLE_MESSAGE =
  "This workspace can no longer be restored.";

export const LEAGUE_ACCESS_VALIDATED_MESSAGE =
  "ESPN access validated. Confirming and claiming this league is the next step.";

export const CLAIM_ACTION_LABEL =
  "Connect this ESPN league to my Fantasy What-If workspace.";

export const LEAGUE_CONNECTED_MESSAGE =
  "Your ESPN league is connected. Initial synchronization is the next step.";

export const LEAGUE_DATA_READY_MESSAGE =
  "Your league data is ready. Shared spectator access is the next step.";

export const SPECTATOR_ACCESS_READY_MESSAGE =
  "Shared spectator access is ready. Workspace activation is the final step.";

export const ACTIVATE_WORKSPACE_LABEL = "Activate workspace";

export const START_INITIAL_SYNC_LABEL = "Start initial synchronization";

export const RETRY_INITIAL_SYNC_LABEL = "Try initial synchronization again";

export const LEAGUE_ALREADY_CLAIMED_MESSAGE =
  "This fantasy league is already connected to Fantasy What-If. Contact the league owner and ask for the workspace's spectator access if you need viewing access.";

export const CONNECTION_ERROR_MESSAGE =
  "Unable to reach Fantasy What-If. Check your connection and try again.";

/** Human-readable wait time from a Retry-After value in seconds. */
export function formatRetryAfter(
  seconds: number | null,
): string {
  if (seconds === null || seconds <= 0) {
    return "a few minutes";
  }

  if (seconds < 60) {
    return seconds === 1 ? "1 second" : `${seconds} seconds`;
  }

  const minutes = Math.ceil(seconds / 60);

  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

/**
 * Throttle message built from Retry-After only.
 *
 * Internal throttle buckets and identifiers are never shown.
 */
export function throttledMessage(
  error: ApiError,
  action: string,
): string {
  return `Too many ${action} attempts. Try again in ${formatRetryAfter(
    error.retryAfterSeconds,
  )}.`;
}

/**
 * Safe fallback for an unexpected failure.
 *
 * Raw exception messages and response bodies are never shown; an
 * ApiError detail is shown only when the backend supplied a stable code,
 * because coded responses carry deliberately user-safe messages.
 */
export function unexpectedErrorMessage(
  error: unknown,
): string {
  if (error instanceof ApiError) {
    if (error.code !== null) {
      return error.detail;
    }

    if (error.status >= 500) {
      return "Fantasy What-If is temporarily unavailable. Try again shortly.";
    }

    return "The request could not be completed. Check the form and try again.";
  }

  return CONNECTION_ERROR_MESSAGE;
}
