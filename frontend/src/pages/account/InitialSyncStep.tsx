import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getInitialSyncProgress,
  onboardingQueryKeys,
  startInitialSync,
} from "../../api/onboarding";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import {
  CONNECTION_ERROR_MESSAGE,
  LEAGUE_CONNECTED_MESSAGE,
  RETRY_INITIAL_SYNC_LABEL,
  START_INITIAL_SYNC_LABEL,
} from "../../auth/accountMessages";
import { FormAlert } from "../../components/account/FormAlert";
import type { OnboardingStage } from "../../types/auth";
import type { InitialSyncProgress } from "../../types/onboarding";

/** Bounded polling interval while the first import is queued or running. */
const INITIAL_SYNC_POLL_INTERVAL_MS = 3_000;

const TERMINAL_STAGES: ReadonlySet<OnboardingStage> = new Set([
  "spectator_setup_pending",
  "initial_sync_failed",
]);

const SAFE_START_CODES = new Set(["onboarding_stage_invalid", "provider_target_unavailable"]);

function startErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.code !== null && SAFE_START_CODES.has(error.code)
      ? error.detail
      : "The first import could not be started. Try again.";
  }

  return CONNECTION_ERROR_MESSAGE;
}

function isTerminal(progress: InitialSyncProgress | undefined): boolean {
  return progress !== undefined && TERMINAL_STAGES.has(progress.onboarding_stage);
}

/**
 * Start, follow and retry the first ESPN import.
 *
 * The server owns all league, season and credential data; this step only
 * asks it to queue work and reads safe progress.
 */
export function InitialSyncStep({ stage }: { stage: OnboardingStage }) {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);
  const refreshedRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const running = stage === "initial_sync_running";
  const failed = stage === "initial_sync_failed";

  const progress = useQuery({
    queryKey: onboardingQueryKeys.synchronization(),
    queryFn: ({ signal }) => getInitialSyncProgress(signal),
    enabled: running || failed,
    refetchInterval: (query) =>
      running && !isTerminal(query.state.data) ? INITIAL_SYNC_POLL_INTERVAL_MS : false,
  });

  const reachedTerminal = running && isTerminal(progress.data);

  useEffect(() => {
    // Success or terminal failure: follow /api/auth/me exactly once.
    if (reachedTerminal && !refreshedRef.current) {
      refreshedRef.current = true;
      void auth.refreshUser();
    }
  }, [auth, reachedTerminal]);

  async function handleStart() {
    if (startingRef.current) {
      return;
    }

    startingRef.current = true;
    setStarting(true);
    setError(null);

    try {
      const accepted = await startInitialSync(auth.csrfToken);
      // Replace any earlier terminal progress so polling follows this attempt.
      queryClient.setQueryData(onboardingQueryKeys.synchronization(), accepted);
      refreshedRef.current = false;
      // /api/auth/me now reports initial_sync_running; the page follows it.
      await auth.refreshUser();
    } catch (startError) {
      setError(startErrorMessage(startError));
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  }

  const job = progress.data?.job ?? null;

  return (
    <section className="provider-setup" aria-labelledby="initial-sync-heading">
      <FormAlert tone="success">{LEAGUE_CONNECTED_MESSAGE}</FormAlert>

      <h2 id="initial-sync-heading">Import your league</h2>

      {running ? (
        <div className="auth-stack" role="status" aria-live="polite">
          <p className="provider-setup-copy">
            {job?.detail ?? "The first import is queued."}
          </p>
          <p className="provider-setup-copy">
            The first import may take several minutes. You can leave this page and sign in
            again later; progress is saved.
          </p>
        </div>
      ) : failed ? (
        <>
          <FormAlert tone="error">
            {job?.status === "failed" || job?.status === "canceled"
              ? job.detail
              : "The first import did not finish. Try again."}
          </FormAlert>
          {error ? <FormAlert tone="error">{error}</FormAlert> : null}
          <button
            type="button"
            className="auth-submit"
            disabled={starting}
            aria-busy={starting}
            onClick={() => {
              void handleStart();
            }}
          >
            {starting ? "Starting…" : RETRY_INITIAL_SYNC_LABEL}
          </button>
        </>
      ) : (
        <>
          <p className="provider-setup-copy">
            Your ESPN league is ready for its first import. Fantasy What-If will load teams,
            schedules and results. The first import may take several minutes.
          </p>
          {error ? <FormAlert tone="error">{error}</FormAlert> : null}
          <button
            type="button"
            className="auth-submit"
            disabled={starting}
            aria-busy={starting}
            onClick={() => {
              void handleStart();
            }}
          >
            {starting ? "Starting…" : START_INITIAL_SYNC_LABEL}
          </button>
        </>
      )}
    </section>
  );
}
