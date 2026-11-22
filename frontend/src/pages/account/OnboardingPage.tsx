import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Flag } from "lucide-react";

import { useAuth } from "../../auth/AuthProvider";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";

/**
 * Safe resume point for a provisional administrator.
 *
 * Provider connection, league claiming, and initial synchronization are
 * the next product slice. This page never links to legacy setup routes.
 */
export function OnboardingPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

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
          <p>
            Connecting your fantasy provider and claiming your league will
            open in the next product release. Your progress is saved, so you
            can sign in again with your administrator email to continue.
          </p>
        </>
      }
    >
      <div className="auth-stack">
        <FormAlert tone="notice">
          League data, standings, and spectator access become available
          after provider setup is complete.
        </FormAlert>

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
