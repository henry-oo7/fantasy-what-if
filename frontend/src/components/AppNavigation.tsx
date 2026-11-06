import {
  BarChart3,
  GitCompareArrows,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import "./AppNavigation.css";

export function AppNavigation() {
  return (
    <nav
      className="app-navigation"
      aria-label="Primary navigation"
    >
      <NavLink
        to="/"
        end
        className={({ isActive }) =>
          isActive
            ? "navigation-link navigation-link-active"
            : "navigation-link"
        }
      >
        <BarChart3 aria-hidden="true" />
        Overview
      </NavLink>

      <NavLink
        to="/schedule-swap"
        className={({ isActive }) =>
          isActive
            ? "navigation-link navigation-link-active"
            : "navigation-link"
        }
      >
        <GitCompareArrows aria-hidden="true" />
        Schedule swap
      </NavLink>
    </nav>
  );
}
