import { Navigate, useLocation } from "react-router-dom";
import { RefreshCw } from "lucide-react";

import { useAuth } from "./AuthProvider";

interface RedirectState {
  from: string;
}

export function ProtectedRoute({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "loading") {
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

  if (auth.status === "error") {
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

  return children;
}
