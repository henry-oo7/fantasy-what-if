import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { OnboardingPage } from "../pages/account/OnboardingPage";
import { PersonalShell } from "../components/layout/PersonalShell";
import { PersonalVerificationPage } from "../pages/personal/PersonalVerificationPage";
import {
  WorkspaceDeletionRecoveryPage,
  WorkspaceDeletionScheduledPage,
} from "../pages/account/WorkspaceDeletionRecoveryPage";
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

  if (auth.workspaceAccess === "personal") {
    const personalReturn = safeReturnPath(
      (location.state as { from?: unknown } | null)?.from,
    );

    return (
      <Navigate
        to={personalReturn?.startsWith("/account") ? personalReturn : "/account"}
        replace
      />
    );
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
    // A losing duplicate claim removed this registration: show the public
    // outcome rather than a generic sign-in page.
    return (
      <Navigate
        to={
          auth.notice === "league_already_claimed"
            ? "/league-already-connected"
            : "/login"
        }
        replace
      />
    );
  }

  if (auth.workspaceAccess === "checking") {
    return <SessionCheckScreen />;
  }

  if (auth.workspaceAccess === "personal") {
    return <Navigate to="/account" replace />;
  }

  if (auth.workspaceAccess === "deletion_recovery") {
    return <Navigate to="/workspace-deletion" replace />;
  }

  if (
    auth.workspaceAccess === "suspended" ||
    auth.workspaceAccess === "inactive"
  ) {
    return <WorkspaceUnavailablePage reason={auth.workspaceAccess} />;
  }

  if (
    auth.user.legacy_workspace?.role !== "admin" ||
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

interface DeletionScheduledState {
  deletionScheduledFor?: unknown;
}

/**
 * Restricted FR-20 recovery screen.
 *
 * A deletion_recovery session sees only this page. Directly after a
 * deletion request the session is gone, so an anonymous visitor carrying
 * the scheduled time in navigation state sees a sign-in prompt instead.
 * Everyone else is routed away.
 */
export function WorkspaceDeletionRoute() {
  const auth = useAuth();
  const location = useLocation();
  const scheduledFor = (location.state as DeletionScheduledState | null)
    ?.deletionScheduledFor;

  if (auth.status === "loading") {
    return <SessionCheckScreen />;
  }

  if (auth.status === "error") {
    return <SessionErrorScreen />;
  }

  if (auth.status !== "authenticated" || auth.user === null) {
    if (typeof scheduledFor === "string") {
      return <WorkspaceDeletionScheduledPage scheduledFor={scheduledFor} />;
    }

    return <Navigate to="/login" replace />;
  }

  if (auth.workspaceAccess === "checking") {
    return <SessionCheckScreen />;
  }

  if (auth.workspaceAccess !== "deletion_recovery") {
    return <Navigate to="/" replace />;
  }

  return <WorkspaceDeletionRecoveryPage />;
}

/**
 * Every personal-account route (/account/**). Anonymous visitors sign in and
 * return here; legacy workspace users are routed to the workspace they
 * already have. GET /api/auth/me navigation_state is the only account-state
 * authority: an unverified account sees only the verification page, and an
 * account that must import its first League sees only the import page.
 */
export function PersonalAccountRoute() {
  const auth = useAuth();
  const location = useLocation();

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
        state={{ from: location.pathname + location.search }}
      />
    );
  }

  if (auth.workspaceAccess === "checking") {
    return <SessionCheckScreen />;
  }

  if (auth.workspaceAccess !== "personal") {
    return <Navigate to="/" replace />;
  }

  const state = auth.user.navigation_state;

  if (state === "email_verification_required") {
    return <PersonalVerificationPage />;
  }

  // An account with no Leagues can still reach its settings to delete itself.
  if (
    state === "import_required" &&
    !["/account/import", "/account/settings"].includes(
      location.pathname.replace(/\/+$/, ""),
    )
  ) {
    return <Navigate to="/account/import" replace />;
  }

  return <PersonalShell />;
}
