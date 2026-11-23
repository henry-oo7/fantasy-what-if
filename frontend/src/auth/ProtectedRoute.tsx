import { Navigate, useLocation } from "react-router-dom";
import { RefreshCw } from "lucide-react";

import { WorkspaceUnavailablePage } from "../pages/account/WorkspaceUnavailablePage";
import { useAuth } from "./AuthProvider";

interface RedirectState {
  from: string;
}

export function SessionCheckScreen() {
  return (
    <main
      className="app-state"
      aria-live="polite"
    >
      <RefreshCw
        className="spinner"
        aria-hidden="true"
      />
      <h1>Checking your session</h1>
      <p>
        Confirming access to the fantasy league.
      </p>
    </main>
  );
}

export function SessionErrorScreen() {
  const auth = useAuth();

  return (
    <main
      className="app-state"
      aria-live="assertive"
    >
      <h1>Unable to verify your session</h1>
      <p>{auth.error}</p>
      <button
        type="button"
        onClick={() => {
          void auth.refreshUser();
        }}
      >
        Try again
      </button>
    </main>
  );
}

/**
 * Application routes for authenticated users of an active workspace.
 *
 * Provisional administrators are sent to the onboarding resume page and
 * never reach the active application or legacy setup. Suspended and
 * inactive workspaces receive a dedicated blocked state.
 */
export function ProtectedRoute({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "loading") {
    return <SessionCheckScreen />;
  }

  if (auth.status === "error") {
    return <SessionErrorScreen />;
  }

  if (
    auth.status !== "authenticated" ||
    auth.user === null
  ) {
    const state: RedirectState = {
      from:
        location.pathname +
        location.search,
    };

    return (
      <Navigate
        to="/login"
        replace
        state={state}
      />
    );
  }

  if (auth.workspaceAccess === "checking") {
    return <SessionCheckScreen />;
  }

  if (auth.workspaceAccess === "provisional") {
    return (
      <Navigate
        to="/onboarding"
        replace
      />
    );
  }

  if (auth.workspaceAccess === "deletion_recovery") {
    return (
      <Navigate
        to="/workspace-deletion"
        replace
      />
    );
  }

  if (
    auth.workspaceAccess === "suspended" ||
    auth.workspaceAccess === "inactive"
  ) {
    return (
      <WorkspaceUnavailablePage
        reason={auth.workspaceAccess}
      />
    );
  }

  return children;
}
