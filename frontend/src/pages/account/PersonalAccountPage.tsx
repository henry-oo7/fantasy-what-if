import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { UserRound } from "lucide-react";

import { useAuth } from "../../auth/AuthProvider";
import { AccountLayout } from "../../components/account/AccountLayout";
import type { NavigationState } from "../../types/auth";

const MESSAGES: Partial<Record<NavigationState, string>> = {
  email_verification_required:
    "Verify your email address using the link we sent you.",
  import_required:
    "League setup is ready for the next interface slice. You have no leagues yet.",
  personal_dashboard:
    "Your leagues are available. The league list arrives with the next interface slice.",
};

/**
 * Temporary, bounded home for a workspace-free personal account. The League
 * list, switcher, import form, and dashboard are built in
 * feature/frontend-league-switcher; the import endpoint stays API-only until then.
 */
export function PersonalAccountPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);
  const state = auth.user?.navigation_state;

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
      headingId="personal-account-heading"
      label="Your account"
      title={`Welcome, ${auth.user?.display_name ?? ""}`.trim()}
      icon={<UserRound aria-hidden="true" />}
      description={<p>{(state && MESSAGES[state]) ?? "Your account is ready."}</p>}
    >
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
    </AccountLayout>
  );
}
