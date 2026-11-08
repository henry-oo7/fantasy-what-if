import {
  Navigate,
  Route,
  Routes,
} from "react-router-dom";

import App from "./App";
import { AllPlayPage } from "./pages/AllPlayPage";
import { LeagueAveragePage } from "./pages/LeagueAveragePage";
import { OptimalLineupsPage } from "./pages/OptimalLineupsPage";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./components/layout/AppShell";
import { LoginPage } from "./pages/LoginPage";
import {
  ScheduleSwapPage,
} from "./pages/ScheduleSwapPage";

export function AppRouter() {
  return (
    <Routes>
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
          element={
            <Navigate
              to="/optimal-lineups"
              replace
            />
          }
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
