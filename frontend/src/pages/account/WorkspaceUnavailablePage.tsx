import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Ban } from "lucide-react";

import { useAuth } from "../../auth/AuthProvider";
import { AccountLayout } from "../../components/account/AccountLayout";

interface WorkspaceUnavailablePageProps {
  reason: "suspended" | "inactive";
}

/**
 * Dedicated blocked state for workspace_suspended and workspace_inactive,
 * so lifecycle responses never surface as a generic network error.
 */
export function WorkspaceUnavailablePage({
  reason,
}: WorkspaceUnavailablePageProps) {
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
      headingId="workspace-unavailable-heading"
      label="Workspace access"
      title={
        reason === "suspended"
          ? "This workspace is suspended"
          : "This workspace is not available"
      }
      icon={<Ban aria-hidden="true" />}
      description={
        <p>
          {reason === "suspended"
            ? "Access to this league workspace is currently suspended. League data is not available while the workspace is suspended."
            : "This league workspace is not active right now. Ask your league administrator when it will be available again."}
        </p>
      }
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
