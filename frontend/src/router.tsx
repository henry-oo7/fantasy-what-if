import {
  Navigate,
  Route,
  Routes,
} from "react-router-dom";

import App from "./App";
import { AdminInvitationsPage } from "./pages/AdminInvitationsPage";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./components/layout/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
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
        path="/register"
        element={<RegisterPage />}
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
          path="schedule-swap"
          element={<ScheduleSwapPage />}
        />

        <Route
          path="admin/invitations"
          element={<AdminInvitationsPage />}
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
