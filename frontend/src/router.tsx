import { Navigate, Route, Routes } from "react-router-dom";

import App from "./App";
import { OnboardingRoute, PublicOnlyRoute } from "./auth/AccountRouteGuards";
import { AdminRoute } from "./auth/AdminRoute";
import { InstallationRouteGuard } from "./auth/InstallationRouteGuard";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./components/layout/AppShell";
import { AdminPage } from "./pages/AdminPage";
import { AllPlayPage } from "./pages/AllPlayPage";
import { ForgotPasswordPage } from "./pages/account/ForgotPasswordPage";
import { LeagueAlreadyConnectedPage } from "./pages/account/LeagueAlreadyConnectedPage";
import { LoginPage } from "./pages/account/LoginPage";
import { RegisterPage } from "./pages/account/RegisterPage";
import { ResetPasswordPage } from "./pages/account/ResetPasswordPage";
import { VerifyEmailPage } from "./pages/account/VerifyEmailPage";
import { LeagueAveragePage } from "./pages/LeagueAveragePage";
import { OptimalLineupBreakdownPage } from "./pages/OptimalLineupBreakdownPage";
import { OptimalLineupsPage } from "./pages/OptimalLineupsPage";
import { PlayoffPredictorPage } from "./pages/PlayoffPredictorPage";
import { ScheduleSwapPage } from "./pages/ScheduleSwapPage";
import { SetupPage } from "./pages/SetupPage";

function ApplicationRoutes() {
  return (
    <Routes>
      <Route path="/setup" element={<SetupPage />} />

      <Route
        path="/login"
        element={
          <PublicOnlyRoute>
            <LoginPage />
          </PublicOnlyRoute>
        }
      />

      <Route
        path="/register"
        element={
          <PublicOnlyRoute>
            <RegisterPage />
          </PublicOnlyRoute>
        }
      />

      <Route
        path="/forgot-password"
        element={
          <PublicOnlyRoute>
            <ForgotPasswordPage />
          </PublicOnlyRoute>
        }
      />

      {/* One-time token routes stay reachable with or without a session. */}
      <Route path="/verify-email" element={<VerifyEmailPage />} />

      <Route path="/reset-password" element={<ResetPasswordPage />} />

      <Route path="/onboarding" element={<OnboardingRoute />} />

      {/* Public outcome of a losing duplicate claim; the session is gone. */}
      <Route path="/league-already-connected" element={<LeagueAlreadyConnectedPage />} />

      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route index element={<App />} />

        <Route
          path="admin"
          element={
            <AdminRoute>
              <AdminPage />
            </AdminRoute>
          }
        />

        <Route path="all-play" element={<AllPlayPage />} />

        <Route path="league-average" element={<LeagueAveragePage />} />

        <Route path="schedule-swap" element={<ScheduleSwapPage />} />

        <Route path="playoff-predictor" element={<PlayoffPredictorPage />} />

        <Route path="optimal-lineups" element={<OptimalLineupsPage />} />

        <Route
          path="optimal-lineups/:teamId"
          element={<OptimalLineupBreakdownPage />}
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
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
