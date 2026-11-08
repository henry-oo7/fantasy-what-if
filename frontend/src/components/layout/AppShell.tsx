import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  GitCompareArrows,
  LayoutDashboard,
  LogOut,
  Menu,
  ShieldCheck,
  Target,
  UserPlus,
  X,
} from "lucide-react";
import {
  NavLink,
  Outlet,
  useNavigate,
} from "react-router-dom";

import { currentLeagueQueryOptions } from "../../api/league";
import { useAuth } from "../../auth/AuthProvider";
import "./AppShell.css";

interface NavigationItem {
  label: string;
  path: string;
  icon: typeof LayoutDashboard;
  end?: boolean;
}

const navigationItems: NavigationItem[] = [
  {
    label: "Home",
    path: "/",
    icon: LayoutDashboard,
    end: true,
  },
  {
    label: "All-Play Standings",
    path: "/all-play",
    icon: BarChart3,
  },
  {
    label: "League Average",
    path: "/league-average",
    icon: BarChart3,
  },
  {
    label: "Schedule Swap",
    path: "/schedule-swap",
    icon: GitCompareArrows,
  },
  {
    label: "Optimal Lineups",
    path: "/optimal-lineups",
    icon: Target,
  },
];

export function AppShell() {
  const auth = useAuth();
  const navigate = useNavigate();
  const leagueQuery = useQuery(
    currentLeagueQueryOptions(),
  );
  const [drawerOpen, setDrawerOpen] =
    useState(false);
  const [loggingOut, setLoggingOut] =
    useState(false);
  const [logoutError, setLogoutError] =
    useState<string | null>(null);

  useEffect(() => {
    if (!drawerOpen) {
      return;
    }

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setDrawerOpen(false);
      }
    }

    document.addEventListener(
      "keydown",
      handleEscape,
    );

    return () => {
      document.removeEventListener(
        "keydown",
        handleEscape,
      );
    };
  }, [drawerOpen]);

  if (auth.user === null) {
    return null;
  }

  async function handleLogout() {
    setLoggingOut(true);
    setLogoutError(null);

    try {
      await auth.logoutUser();

      navigate("/login", {
        replace: true,
      });
    } catch {
      setLogoutError(
        "Unable to sign out. Please try again.",
      );
    } finally {
      setLoggingOut(false);
    }
  }

  const userInitial =
    auth.user.display_name
      .trim()
      .charAt(0)
      .toUpperCase() || "U";

  return (
    <div className="application-shell">
      <header className="mobile-app-bar">
        <button
          className="mobile-menu-button"
          type="button"
          aria-label="Open navigation"
          aria-expanded={drawerOpen}
          aria-controls="application-sidebar"
          onClick={() => {
            setDrawerOpen(true);
          }}
        >
          <Menu aria-hidden="true" />
        </button>

        <div className="mobile-brand">
          <span className="brand-mark">
            <BarChart3 aria-hidden="true" />
          </span>

          <span>Fantasy What-If</span>
        </div>

        <div
          className="mobile-user-avatar"
          aria-label={auth.user.display_name}
        >
          {userInitial}
        </div>
      </header>

      <button
        className={
          drawerOpen
            ? "navigation-scrim navigation-scrim-visible"
            : "navigation-scrim"
        }
        type="button"
        aria-label="Close navigation"
        tabIndex={drawerOpen ? 0 : -1}
        onClick={() => {
          setDrawerOpen(false);
        }}
      />

      <aside
        id="application-sidebar"
        className={
          drawerOpen
            ? "application-sidebar application-sidebar-open"
            : "application-sidebar"
        }
      >
        <div className="sidebar-heading">
          <div className="sidebar-brand">
            <span className="brand-mark">
              <BarChart3 aria-hidden="true" />
            </span>

            <div>
              <strong>Fantasy</strong>
              <span>What-If</span>
            </div>
          </div>

          <button
            className="drawer-close-button"
            type="button"
            aria-label="Close navigation"
            onClick={() => {
              setDrawerOpen(false);
            }}
          >
            <X aria-hidden="true" />
          </button>
        </div>

        <div className="sidebar-league">
          <strong>
            {leagueQuery.data?.name ??
              "Jimmy and Friends"}
          </strong>

          <small>
            {leagueQuery.data?.season ??
              auth.user.season ??
              "Current"}{" "}
            season
          </small>
        </div>

        <nav
          className="sidebar-navigation"
          aria-label="Primary navigation"
        >
          <span className="navigation-section-label">
            League
          </span>

          {navigationItems.map((item) => {
            const Icon = item.icon;

            return (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.end}
                onClick={() => {
                  setDrawerOpen(false);
                }}
                className={({ isActive }) =>
                  isActive
                    ? "sidebar-link sidebar-link-active"
                    : "sidebar-link"
                }
              >
                <Icon aria-hidden="true" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}

          <div className="sidebar-coming-soon">
            <span className="navigation-section-label">
              Coming next
            </span>

            <div className="disabled-navigation-item">
              <BarChart3 aria-hidden="true" />
              <span>League Analytics</span>
            </div>
          </div>
        </nav>

        {auth.user.role === "admin" ? (
          <nav
            className="sidebar-admin-navigation"
            aria-label="Administration"
          >
            <span className="navigation-section-label">
              Administration
            </span>

            <NavLink
                to="/admin/invitations"
                onClick={() => {
                  setDrawerOpen(false);
                }}
                className={({ isActive }) =>
                  isActive
                    ? "sidebar-link sidebar-link-active"
                    : "sidebar-link"
                }
              >
                <UserPlus aria-hidden="true" />
                <span>Invitations</span>
              </NavLink>

          </nav>
        ) : null}

        <div className="sidebar-account">
          <div className="sidebar-user">
            <div
              className="sidebar-user-avatar"
              aria-hidden="true"
            >
              {userInitial}
            </div>

            <div className="sidebar-user-details">
              <strong>
                {auth.user.display_name}
              </strong>

              <span>
                {auth.user.team_name ??
                  `@${auth.user.username}`}
              </span>
            </div>

            {auth.user.role === "admin" ? (
              <span
                className="sidebar-admin-icon"
                title="Administrator"
              >
                <ShieldCheck aria-hidden="true" />
              </span>
            ) : null}
          </div>

          <button
            className="sidebar-logout"
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

          {logoutError ? (
            <p
              className="sidebar-error"
              role="alert"
            >
              {logoutError}
            </p>
          ) : null}
        </div>
      </aside>

      <main className="application-content">
        <Outlet />
      </main>
    </div>
  );
}
