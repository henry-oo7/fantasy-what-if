import { Navigate, Route, Routes } from "react-router-dom";

import App from "./App";
import {
  OnboardingRoute,
  PersonalAccountRoute,
  PublicOnlyRoute,
  WorkspaceDeletionRoute,
} from "./auth/AccountRouteGuards";
import { AdminRoute } from "./auth/AdminRoute";
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
import { ImportLeaguePage } from "./pages/personal/ImportLeaguePage";
import { PersonalLanding } from "./pages/personal/PersonalLanding";
import { PersonalSeasonLayout } from "./pages/personal/PersonalSeasonLayout";
import { LeagueAveragePage } from "./pages/LeagueAveragePage";
import { OptimalLineupBreakdownPage } from "./pages/OptimalLineupBreakdownPage";
import { OptimalLineupsPage } from "./pages/OptimalLineupsPage";
import { PlayoffPredictorPage } from "./pages/PlayoffPredictorPage";
import { ScheduleSwapPage } from "./pages/ScheduleSwapPage";

/**
 * SaaS routing. Account pages are public; onboarding and the application
 * are gated by /api/auth/me. There is no installation setup route.
 */
export function AppRouter() {
  return (
    <Routes>
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

      {/* Personal accounts: no workspace, no slug. The Season id in the path is
          only a locator; the backend authorizes every Season read. */}
      <Route path="/account" element={<PersonalAccountRoute />}>
        <Route index element={<PersonalLanding />} />

        <Route path="import" element={<ImportLeaguePage />} />

        <Route path="seasons/:seasonId" element={<PersonalSeasonLayout />}>
          <Route index element={<App />} />
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

        <Route path="*" element={<Navigate to="/account" replace />} />
      </Route>

      {/* FR-20 restricted recovery; never part of the application shell. */}
      <Route path="/workspace-deletion" element={<WorkspaceDeletionRoute />} />

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
