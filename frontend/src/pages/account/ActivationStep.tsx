import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { activateWorkspace, getActivationReview, onboardingQueryKeys } from "../../api/onboarding";
import { ApiError } from "../../api/client";
import { setupQueryKeys } from "../../api/setup";
import { useAuth } from "../../auth/AuthProvider";
import {
  ACTIVATE_WORKSPACE_LABEL,
  CONNECTION_ERROR_MESSAGE,
  SPECTATOR_ACCESS_READY_MESSAGE,
} from "../../auth/accountMessages";
import { FormAlert } from "../../components/account/FormAlert";
import type { ActivationCategory } from "../../types/onboarding";

const CORRECTIVE_MESSAGES: Record<ActivationCategory, string> = {
  provider_connection:
    "The connected league is unavailable. Contact support before activating.",
  synchronization:
    "Initial synchronization has not completed. Activation becomes available after it succeeds.",
  league_data:
    "The imported league data is incomplete. Contact support before activating.",
  spectator_access:
    "Shared spectator access is not configured and enabled. Contact support before activating.",
};

function correctiveMessage(category: unknown): string {
  return typeof category === "string" && Object.hasOwn(CORRECTIVE_MESSAGES, category)
    ? CORRECTIVE_MESSAGES[category as ActivationCategory]
    : "The workspace is not ready to activate.";
}

function activationErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "activation_not_ready") {
      return "The workspace is not ready to activate. Review the checks above.";
    }

    if (error.code === "onboarding_stage_invalid") {
      return error.detail;
    }

    return "The workspace could not be activated. Try again.";
  }

  return CONNECTION_ERROR_MESSAGE;
}

/**
 * Final review and activation. The server rechecks every invariant; this
 * step only summarizes safe state and asks it to activate.
 */
export function ActivationStep() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const review = useQuery({
    queryKey: onboardingQueryKeys.activation(),
    queryFn: ({ signal }) => getActivationReview(signal),
  });

  async function handleActivate() {
    if (pendingRef.current) {
      return;
    }

    pendingRef.current = true;
    setPending(true);
    setError(null);

    try {
      await activateWorkspace(auth.csrfToken);
      // Provisional onboarding data must not survive into the active app.
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== setupQueryKeys.all[0],
      });
      // /api/auth/me now reports an active workspace; routing opens the app.
      await auth.refreshUser();
    } catch (activationError) {
      setError(activationErrorMessage(activationError));
      void review.refetch();
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  return (
    <section className="provider-setup" aria-labelledby="activation-heading">
      <FormAlert tone="success">{SPECTATOR_ACCESS_READY_MESSAGE}</FormAlert>

      <h2 id="activation-heading">Review and activate</h2>

      {review.isPending ? (
        <p className="provider-setup-copy" role="status">
          Checking your workspace…
        </p>
      ) : review.isError ? (
        <FormAlert tone="error">
          {review.error instanceof ApiError && review.error.code === "onboarding_stage_invalid"
            ? review.error.detail
            : "The activation review could not be loaded. Try again."}
        </FormAlert>
      ) : (
        <>
          <dl className="league-target" aria-label="Workspace summary">
            <div>
              <dt>Provider</dt>
              <dd>{review.data.provider === "espn" ? "ESPN" : "Not connected"}</dd>
            </div>
            <div>
              <dt>Workspace</dt>
              <dd>{review.data.workspace_slug}</dd>
            </div>
            <div>
              <dt>League ID</dt>
              <dd>{review.data.league_id}</dd>
            </div>
            <div>
              <dt>Initial season</dt>
              <dd>{review.data.season_year}</dd>
            </div>
            <div>
              <dt>Synchronization</dt>
              <dd>{review.data.synchronization_complete ? "Complete" : "Not complete"}</dd>
            </div>
            <div>
              <dt>Spectator access</dt>
              <dd>{review.data.spectator_access_ready ? "Configured" : "Not configured"}</dd>
            </div>
          </dl>

          {review.data.ready ? (
            <p className="provider-setup-copy">
              Activation opens your synchronized workspace to you and to league members
              who sign in with the shared spectator password.
            </p>
          ) : (
            <FormAlert tone="notice">{correctiveMessage(review.data.blocking_category)}</FormAlert>
          )}

          {error ? <FormAlert tone="error">{error}</FormAlert> : null}

          <button
            type="button"
            className="auth-submit"
            disabled={pending || !review.data.ready}
            aria-busy={pending}
            onClick={() => {
              void handleActivate();
            }}
          >
            {pending ? "Activating…" : ACTIVATE_WORKSPACE_LABEL}
          </button>
        </>
      )}
    </section>
  );
}
