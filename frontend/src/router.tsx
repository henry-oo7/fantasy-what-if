import {
  Navigate,
  Route,
  Routes,
} from "react-router-dom";

import App from "./App";
import { AdminRoute } from "./auth/AdminRoute";
import { InstallationRouteGuard } from "./auth/InstallationRouteGuard";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./components/layout/AppShell";
import { AdminPage } from "./pages/AdminPage";
import { AllPlayPage } from "./pages/AllPlayPage";
import { LeagueAveragePage } from "./pages/LeagueAveragePage";
import { LoginPage } from "./pages/LoginPage";
import {
  OptimalLineupBreakdownPage,
} from "./pages/OptimalLineupBreakdownPage";
import { OptimalLineupsPage } from "./pages/OptimalLineupsPage";
import {
  ScheduleSwapPage,
} from "./pages/ScheduleSwapPage";
import { SetupPage } from "./pages/SetupPage";


function ApplicationRoutes() {
  return (
    <Routes>
      <Route
        path="/setup"
        element={<SetupPage />}
      />

      <Route
        path="/login"
        element={<LoginPage />}
      />

      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route
          index
          element={<App />}
        />

        <Route
          path="admin"
          element={
            <AdminRoute>
              <AdminPage />
            </AdminRoute>
          }
        />

        <Route
          path="all-play"
          element={<AllPlayPage />}
        />

        <Route
          path="league-average"
          element={<LeagueAveragePage />}
        />

        <Route
          path="schedule-swap"
          element={<ScheduleSwapPage />}
        />

        <Route
          path="optimal-lineups"
          element={<OptimalLineupsPage />}
        />

        <Route
          path="optimal-lineups/:teamId"
          element={<OptimalLineupBreakdownPage />}
        />
      </Route>

      <Route
        path="*"
        element={
          <Navigate
            to="/"
            replace
          />
        }
      />
    </Routes>
  );
}


export function AppRouter() {
  return (
    <InstallationRouteGuard>
      <ApplicationRoutes />
    </InstallationRouteGuard>
  );
}
