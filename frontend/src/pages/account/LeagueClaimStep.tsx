import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  claimProviderLeague,
  getProviderTarget,
  onboardingQueryKeys,
} from "../../api/onboarding";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import {
  CLAIM_ACTION_LABEL,
  CONNECTION_ERROR_MESSAGE,
  LEAGUE_ACCESS_VALIDATED_MESSAGE,
} from "../../auth/accountMessages";
import { FormAlert } from "../../components/account/FormAlert";

const SAFE_DETAIL_CODES = new Set([
  "onboarding_stage_invalid",
  "provider_target_unavailable",
]);

function claimErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.code !== null && SAFE_DETAIL_CODES.has(error.code)
      ? error.detail
      : "The league could not be connected. Try again.";
  }

  return CONNECTION_ERROR_MESSAGE;
}

/**
 * Confirm and claim the validated ESPN target.
 *
 * Nothing is claimed until the administrator confirms. A losing duplicate
 * claim ends the (already removed) session and clears cached data before
 * the public outcome page renders.
 */
export function LeagueClaimStep() {
  const auth = useAuth();
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const target = useQuery({
    queryKey: onboardingQueryKeys.providerTarget(),
    queryFn: ({ signal }) => getProviderTarget(signal),
  });

  async function handleConfirm() {
    if (pendingRef.current) {
      return;
    }

    pendingRef.current = true;
    setPending(true);
    setError(null);

    try {
      await claimProviderLeague(auth.csrfToken);
      // /api/auth/me now reports initial_sync_pending; the page follows it.
      await auth.refreshUser();
    } catch (claimError) {
      if (claimError instanceof ApiError && claimError.code === "provider_league_already_claimed") {
        // Purge cached data and auth state; the onboarding route then
        // routes to the public outcome page.
        auth.endSession("league_already_claimed");
        return;
      }

      setError(claimErrorMessage(claimError));
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  return (
    <section className="provider-setup" aria-labelledby="league-claim-heading">
      <FormAlert tone="success">{LEAGUE_ACCESS_VALIDATED_MESSAGE}</FormAlert>

      <h2 id="league-claim-heading">Confirm your league</h2>

      {target.isPending ? (
        <p className="provider-setup-copy" role="status">
          Loading the validated league…
        </p>
      ) : target.isError ? (
        <FormAlert tone="error">{claimErrorMessage(target.error)}</FormAlert>
      ) : (
        <>
          <dl className="league-target" aria-label="Validated league">
            <div>
              <dt>Provider</dt>
              <dd>ESPN</dd>
            </div>
            <div>
              <dt>League ID</dt>
              <dd>{target.data.league_id}</dd>
            </div>
            <div>
              <dt>Initial season</dt>
              <dd>{target.data.season_year}</dd>
            </div>
            <div>
              <dt>Access</dt>
              <dd>{target.data.access_mode === "private" ? "Private league" : "Public league"}</dd>
            </div>
          </dl>

          <p className="provider-setup-copy">
            Connecting reserves this league for your workspace. A league can belong to only
            one Fantasy What-If workspace. League data loads in the next step.
          </p>

          {error ? <FormAlert tone="error">{error}</FormAlert> : null}

          <button
            type="button"
            className="auth-submit"
            disabled={pending}
            aria-busy={pending}
            onClick={() => {
              void handleConfirm();
            }}
          >
            {pending ? "Connecting league…" : CLAIM_ACTION_LABEL}
          </button>
        </>
      )}
    </section>
  );
}
