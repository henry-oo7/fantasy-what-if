import { useState } from "react";
import {
  LogOut,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";

export function AuthenticatedUserMenu() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [loggingOut, setLoggingOut] =
    useState(false);
  const [error, setError] =
    useState<string | null>(null);

  if (auth.user === null) {
    return null;
  }

  async function handleLogout() {
    setLoggingOut(true);
    setError(null);

    try {
      await auth.logoutUser();

      navigate("/login", {
        replace: true,
      });
    } catch {
      setError(
        "Unable to complete logout. Please try again.",
      );
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <div className="user-menu">
      <div className="user-menu-identity">
        <div
          className="user-avatar"
          aria-hidden="true"
        >
          <UserRound />
        </div>

        <div className="user-details">
          <strong>{auth.user.display_name}</strong>

          <span>
            {auth.user.team_name ??
              `@${auth.user.username}`}
          </span>
        </div>

        {auth.user.role === "admin" ? (
          <span
            className="admin-badge"
            title="Administrator"
          >
            <ShieldCheck aria-hidden="true" />
            Admin
          </span>
        ) : null}
      </div>

      <button
        className="logout-button"
        type="button"
        disabled={loggingOut}
        onClick={() => {
          void handleLogout();
        }}
      >
        <LogOut aria-hidden="true" />

        {loggingOut
          ? "Signing out..."
          : "Sign out"}
      </button>

      {error ? (
        <p
          className="user-menu-error"
          role="alert"
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}
