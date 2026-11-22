import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { OnboardingPage } from "../pages/account/OnboardingPage";
import { WorkspaceUnavailablePage } from "../pages/account/WorkspaceUnavailablePage";
import { useAuth } from "./AuthProvider";
import { safeReturnPath } from "./returnPath";
import {
  SessionCheckScreen,
  SessionErrorScreen,
} from "./ProtectedRoute";

/**
 * Public account pages (sign in, registration, password request).
 *
 * Visitors without a session see the page immediately; an authenticated
 * user is sent onward instead of seeing a sign-in form.
 */
export function PublicOnlyRoute({
  children,
}: {
  children: ReactNode;
}) {
  const auth = useAuth();
  const location = useLocation();

  if (
    auth.status !== "authenticated" ||
    auth.user === null ||
    auth.workspaceAccess === "checking"
  ) {
    return children;
  }

  if (auth.workspaceAccess === "provisional") {
    return (
      <Navigate
        to="/onboarding"
        replace
      />
    );
  }

  const returnPath =
    auth.workspaceAccess === "active"
      ? safeReturnPath((location.state as { from?: unknown } | null)?.from)
      : null;

  return (
    <Navigate
      to={returnPath ?? "/"}
      replace
    />
  );
}

/**
 * Onboarding resume page for provisional administrators only.
 */
export function OnboardingRoute() {
  const auth = useAuth();

  if (auth.status === "loading") {
    return <SessionCheckScreen />;
  }

  if (auth.status === "error") {
    return <SessionErrorScreen />;
  }

  if (auth.status !== "authenticated" || auth.user === null) {
    return (
      <Navigate
        to="/login"
        replace
      />
    );
  }

  if (auth.workspaceAccess === "checking") {
    return <SessionCheckScreen />;
  }

  if (
    auth.workspaceAccess === "suspended" ||
    auth.workspaceAccess === "inactive"
  ) {
    return <WorkspaceUnavailablePage reason={auth.workspaceAccess} />;
  }

  if (
    auth.user.role !== "admin" ||
    auth.workspaceAccess !== "provisional"
  ) {
    return (
      <Navigate
        to="/"
        replace
      />
    );
  }

  return <OnboardingPage />;
}
