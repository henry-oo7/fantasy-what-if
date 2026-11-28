import {
  BarChart3,
  GitCompareArrows,
  LayoutDashboard,
  Settings,
  Target,
  Trophy,
} from "lucide-react";
import { NavLink } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { useSeasonScope } from "../../personal/seasonScope";
import { LeagueSwitcher } from "./LeagueSwitcher";
import { ShellFrame } from "./ShellFrame";

const SECTIONS = [
  { label: "Season overview", path: "", icon: LayoutDashboard },
  { label: "All-Play Standings", path: "/all-play", icon: BarChart3 },
  { label: "League Average", path: "/league-average", icon: BarChart3 },
  { label: "Schedule Swap", path: "/schedule-swap", icon: GitCompareArrows },
  { label: "Playoff Predictor", path: "/playoff-predictor", icon: Trophy },
  { label: "Optimal Lineups", path: "/optimal-lineups", icon: Target },
] as const;

/** Shell of a signed-in personal account: League switcher plus the selected Season's analytics. */
export function PersonalShell() {
  const auth = useAuth();
  const { seasonId, basePath } = useSeasonScope();

  return (
    <ShellFrame
      context={(closeDrawer) => <LeagueSwitcher onNavigate={closeDrawer} />}
      navigation={(closeDrawer) =>
        seasonId === undefined ? null : (
          <nav className="sidebar-navigation" aria-label="Season navigation">
            <span className="navigation-section-label">Season</span>

            {SECTIONS.map(({ label, path, icon: Icon }) => (
              <NavLink
                key={label}
                to={`${basePath}${path}`}
                end={path === ""}
                onClick={closeDrawer}
                className={({ isActive }) =>
                  isActive ? "sidebar-link sidebar-link-active" : "sidebar-link"
                }
              >
                <Icon aria-hidden="true" />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
        )
      }
      account={(closeDrawer, userInitial) => (
        <>
          <NavLink
            to="/account/settings"
            onClick={closeDrawer}
            className={({ isActive }) =>
              isActive ? "sidebar-link sidebar-link-active" : "sidebar-link"
            }
          >
            <Settings aria-hidden="true" />
            <span>Account settings</span>
          </NavLink>

          <div className="sidebar-user">
            <div className="sidebar-user-avatar" aria-hidden="true">
              {userInitial}
            </div>

            <div className="sidebar-user-details">
              <strong>{auth.user?.display_name}</strong>
              <span>Personal account</span>
            </div>
          </div>
        </>
      )}
    />
  );
}
