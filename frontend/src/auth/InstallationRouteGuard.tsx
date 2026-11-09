import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Navigate,
  useLocation,
} from "react-router-dom";
import {
  RefreshCw,
  ServerOff,
} from "lucide-react";

import { ApiError } from "../api/client";
import {
  setupStatusQueryOptions,
} from "../api/setup";


interface InstallationRouteGuardProps {
  children: ReactNode;
}


function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to determine the installation status.";
}


export function InstallationRouteGuard({
  children,
}: InstallationRouteGuardProps) {
  const location = useLocation();
  const setupQuery = useQuery(
    setupStatusQueryOptions(),
  );

  if (setupQuery.isPending) {
    return (
      <main
        className="installation-state"
        aria-live="polite"
      >
        <RefreshCw
          className="spinner"
          aria-hidden="true"
        />

        <p className="installation-state-kicker">
          Fantasy What-If
        </p>

        <h1>Checking this installation</h1>

        <p>
          Loading the first-run setup state before
          opening the application.
        </p>
      </main>
    );
  }

  if (setupQuery.isError) {
    return (
      <main
        className="installation-state"
        aria-live="assertive"
      >
        <ServerOff aria-hidden="true" />

        <p className="installation-state-kicker">
          Connection unavailable
        </p>

        <h1>Unable to reach the backend</h1>

        <p>
          {errorMessage(setupQuery.error)}
        </p>

        <button
          type="button"
          onClick={() => {
            void setupQuery.refetch();
          }}
        >
          Try again
        </button>
      </main>
    );
  }

  const setup = setupQuery.data;
  const isSetupRoute =
    location.pathname === "/setup";

  const isLoginRoute =
    location.pathname === "/login";

  if (
    setup.status !== "active" &&
    !isSetupRoute &&
    !(
      setup.administrator_configured &&
      isLoginRoute
    )
  ) {
    return (
      <Navigate
        to="/setup"
        replace
      />
    );
  }

  if (
    setup.status === "active" &&
    isSetupRoute
  ) {
    return (
      <Navigate
        to="/"
        replace
      />
    );
  }

  return children;
}
