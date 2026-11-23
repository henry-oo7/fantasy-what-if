import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Flag } from "lucide-react";

import { useAuth } from "../../auth/AuthProvider";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";
import { onboardingStageMessage } from "../../auth/workspaceLifecycle";
import { LEAGUE_CONNECTED_MESSAGE } from "../../auth/accountMessages";
import { LeagueClaimStep } from "./LeagueClaimStep";
import { ProviderSetupStep } from "./ProviderSetupStep";

/**
 * Resume point for a provisional administrator, driven by the onboarding
 * stage from GET /api/auth/me.
 *
 * - provider_setup_pending / provider_validation_failed: provider step
 * - league_claim_pending: confirm and claim the validated league
 * - initial_sync_pending: connected placeholder (synchronization is next)
 * - any other stage: saved-progress placeholder
 *
 * This page never links to legacy setup routes.
 */
export function OnboardingPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);
  const stage = auth.user?.workspace.onboarding_stage ?? null;
  const stageMessage = onboardingStageMessage(stage);
  const providerStep =
    stage === "provider_setup_pending" || stage === "provider_validation_failed";
  const claimStep = stage === "league_claim_pending";
  const leagueConnected = stage === "initial_sync_pending";

  async function handleSignOut() {
    setSigningOut(true);

    try {
      await auth.logoutUser();
    } finally {
      setSigningOut(false);
      navigate("/login", { replace: true });
    }
  }

  return (
    <AccountLayout
      headingId="onboarding-heading"
      label="Workspace setup"
      title="Your workspace is reserved"
      icon={<Flag aria-hidden="true" />}
      description={
        <>
          <p>
            {auth.user ? (
              <>
                Signed in as{" "}
                <span className="auth-email">{auth.user.display_name}</span>.{" "}
              </>
            ) : null}
            Your email is verified and your league workspace has been
            created.
          </p>
          {stageMessage ? (
            <p data-testid="onboarding-stage">
              <strong>{stageMessage}</strong>
            </p>
          ) : null}
          <p>
            Your progress is saved, so you can sign in again with your
            administrator email to continue.
          </p>
        </>
      }
    >
      <div className="auth-stack">
        {providerStep ? (
          <ProviderSetupStep priorFailure={stage === "provider_validation_failed"} />
        ) : claimStep ? (
          <LeagueClaimStep />
        ) : leagueConnected ? (
          <FormAlert tone="success">{LEAGUE_CONNECTED_MESSAGE}</FormAlert>
        ) : (
          <FormAlert tone="notice">
            League data, standings, and spectator access become available
            after setup is complete.
          </FormAlert>
        )}

        <button
          type="button"
          className="auth-secondary-button"
          disabled={signingOut}
          aria-busy={signingOut}
          onClick={() => {
            void handleSignOut();
          }}
        >
          {signingOut ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </AccountLayout>
  );
}
