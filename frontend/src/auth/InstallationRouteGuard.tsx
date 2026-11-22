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


/**
 * SaaS account routes never depend on legacy single-installation setup.
 */
const SAAS_ACCOUNT_PATHS: ReadonlySet<string> = new Set([
  "/login",
  "/register",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
  "/onboarding",
]);


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

  // Legacy setup applies only while a legacy installation exists or the
  // one-time bootstrap is genuinely available. A SaaS deployment with no
  // legacy installation reports setup_required with bootstrap unavailable
  // and must reach the ordinary authenticated application.
  const legacySetupApplies =
    setup.installation_exists ||
    setup.bootstrap_available;

  if (SAAS_ACCOUNT_PATHS.has(location.pathname)) {
    return children;
  }

  if (isSetupRoute && !legacySetupApplies) {
    return (
      <Navigate
        to="/"
        replace
      />
    );
  }

  if (
    setup.status !== "active" &&
    legacySetupApplies &&
    !isSetupRoute
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
