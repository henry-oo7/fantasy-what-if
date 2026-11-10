import { useEffect, useState } from "react";
import {
  BarChart3,
  GitCompareArrows,
  LayoutDashboard,
  LogOut,
  Menu,
  ShieldCheck,
  Target,
  X,
} from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { SeasonMenu } from "./SeasonMenu";
import "./AppShell.css";

function FootballIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M4.6 18.8C1.8 16 3.3 10.1 7.9 5.5C12.5.9 18.4-.6 21.2 2.2C24 5 22.5 10.9 17.9 15.5C13.3 20.1 7.4 21.6 4.6 18.8Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="square"
        strokeLinejoin="miter"
      />
      <path
        d="M7 17L19.4 4.6"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="square"
      />
      <path
        d="M9.2 9.6L14.4 14.8M11 7.8L16.2 13M12.8 6L18 11.2"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="square"
      />
    </svg>
  );
}

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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  useEffect(() => {
    if (!drawerOpen) {
      return;
    }

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setDrawerOpen(false);
      }
    }

    document.addEventListener("keydown", handleEscape);

    return () => {
      document.removeEventListener("keydown", handleEscape);
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
      setLogoutError("Unable to sign out. Please try again.");
    } finally {
      setLoggingOut(false);
    }
  }

  const userInitial =
    auth.user.display_name.trim().charAt(0).toUpperCase() || "U";

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
            <FootballIcon aria-hidden="true" />
          </span>

          <span>Fantasy What-If</span>
        </div>

        <div className="mobile-user-avatar" aria-label={auth.user.display_name}>
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
              <FootballIcon aria-hidden="true" />
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

        <SeasonMenu
          onNavigate={() => {
            setDrawerOpen(false);
          }}
        />

        <nav className="sidebar-navigation" aria-label="Primary navigation">
          <span className="navigation-section-label">League</span>

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
                  isActive ? "sidebar-link sidebar-link-active" : "sidebar-link"
                }
              >
                <Icon aria-hidden="true" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>

        <div className="sidebar-account">
          {auth.user.role === "admin" ? (
            <NavLink
              to="/admin"
              className={({ isActive }) =>
                isActive
                  ? "sidebar-user sidebar-user-link sidebar-user-link-active"
                  : "sidebar-user sidebar-user-link"
              }
              aria-label="Open administration"
              onClick={() => {
                setDrawerOpen(false);
              }}
            >
              <div className="sidebar-user-avatar" aria-hidden="true">
                {userInitial}
              </div>

              <div className="sidebar-user-details">
                <strong>{auth.user.display_name}</strong>

                <span>Administrator</span>
              </div>

              <span className="sidebar-admin-icon" title="Open administration">
                <ShieldCheck aria-hidden="true" />
              </span>
            </NavLink>
          ) : (
            <div className="sidebar-user">
              <div className="sidebar-user-avatar" aria-hidden="true">
                {userInitial}
              </div>

              <div className="sidebar-user-details">
                <strong>{auth.user.display_name}</strong>

                <span>Spectator</span>
              </div>
            </div>
          )}

          <button
            className="sidebar-logout"
            type="button"
            disabled={loggingOut}
            onClick={() => {
              void handleLogout();
            }}
          >
            <LogOut aria-hidden="true" />

            {loggingOut ? "Signing out..." : "Sign out"}
          </button>

          {logoutError ? (
            <p className="sidebar-error" role="alert">
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
