import {
  BarChart3,
  GitCompareArrows,
  LayoutDashboard,
  ShieldCheck,
  Target,
  Trophy,
} from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { SeasonMenu } from "./SeasonMenu";
import { ShellFrame } from "./ShellFrame";

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
    label: "Playoff Predictor",
    path: "/playoff-predictor",
    icon: Trophy,
  },
  {
    label: "Optimal Lineups",
    path: "/optimal-lineups",
    icon: Target,
  },
];

export function AppShell() {
  const auth = useAuth();
  const location = useLocation();
  const user = auth.user;

  if (user === null) {
    return null;
  }

  return (
    <ShellFrame
      context={(closeDrawer) => <SeasonMenu onNavigate={closeDrawer} />}
      navigation={(closeDrawer) => (
        <nav className="sidebar-navigation" aria-label="Primary navigation">
          <span className="navigation-section-label">League</span>

          {navigationItems.map((item) => {
            const Icon = item.icon;

            return (
              <NavLink
                key={item.path}
                to={{
                  pathname: item.path,
                  search: location.search,
                }}
                end={item.end}
                onClick={() => {
                  closeDrawer();
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
      )}
      account={(closeDrawer, userInitial) => (
        <>
          {user.legacy_workspace?.role === "admin" ? (
            <NavLink
              to={{
                pathname: "/admin",
                search: location.search,
              }}
              className={({ isActive }) =>
                isActive
                  ? "sidebar-user sidebar-user-link sidebar-user-link-active"
                  : "sidebar-user sidebar-user-link"
              }
              aria-label="Open administration"
              onClick={() => {
                closeDrawer();
              }}
            >
              <div className="sidebar-user-avatar" aria-hidden="true">
                {userInitial}
              </div>

              <div className="sidebar-user-details">
                <strong>{user.display_name}</strong>

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
                <strong>{user.display_name}</strong>

                <span>Spectator</span>
              </div>
            </div>
          )}

        </>
      )}
    />
  );
}
