import {
  useState,
} from "react";
import {
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  RefreshCw,
  ShieldCheck,
  UserRoundCog,
} from "lucide-react";

import {
  adminQueryKeys,
  resetSpectatorPassword,
  runSynchronization,
  setSpectatorAccess,
  spectatorQueryOptions,
  syncStatusQueryOptions,
} from "../api/admin";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import "./admin.css";


function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to complete the administration request.";
}


function formatTimestamp(
  value: string | null,
): string {
  if (value === null) {
    return "Not yet synchronized";
  }

  return new Intl.DateTimeFormat(
    undefined,
    {
      dateStyle: "medium",
      timeStyle: "short",
    },
  ).format(new Date(value));
}


export function AdminPage() {
  const auth = useAuth();
  const queryClient = useQueryClient();

  const spectatorQuery = useQuery(
    spectatorQueryOptions(),
  );

  const syncQuery = useQuery(
    syncStatusQueryOptions(),
  );

  const [password, setPassword] =
    useState("");
  const [
    confirmPassword,
    setConfirmPassword,
  ] = useState("");
  const [showPassword, setShowPassword] =
    useState(false);

  const [spectatorMessage, setSpectatorMessage] =
    useState<string | null>(null);
  const [spectatorError, setSpectatorError] =
    useState<string | null>(null);
  const [savingSpectator, setSavingSpectator] =
    useState(false);

  const [syncMessage, setSyncMessage] =
    useState<string | null>(null);
  const [syncError, setSyncError] =
    useState<string | null>(null);
  const [runningSync, setRunningSync] =
    useState<"incremental" | "full" | null>(
      null,
    );

  const csrfToken = auth.csrfToken;

  async function refreshSpectator() {
    await queryClient.invalidateQueries({
      queryKey: adminQueryKeys.spectator(),
    });
  }

  async function refreshSyncStatus() {
    await queryClient.invalidateQueries({
      queryKey: adminQueryKeys.syncStatus(),
    });
  }

  async function handlePasswordReset() {
    setSpectatorError(null);
    setSpectatorMessage(null);

    if (!csrfToken) {
      setSpectatorError(
        "Your administrator session does not have a CSRF token. Sign in again.",
      );
      return;
    }

    if (password !== confirmPassword) {
      setSpectatorError(
        "Passwords do not match.",
      );
      return;
    }

    if (password.length < 12) {
      setSpectatorError(
        "The shared password must contain at least 12 characters.",
      );
      return;
    }

    setSavingSpectator(true);

    try {
      const response =
        await resetSpectatorPassword(
          password,
          csrfToken,
        );

      setPassword("");
      setConfirmPassword("");

      setSpectatorMessage(
        response.revoked_sessions
          ? `Password updated. ${response.revoked_sessions} spectator session(s) were signed out.`
          : "Password updated. Future spectator sign-ins must use the new password.",
      );

      await refreshSpectator();
    } catch (currentError) {
      setSpectatorError(
        errorMessage(currentError),
      );
    } finally {
      setSavingSpectator(false);
    }
  }

  async function handleAccessChange(
    isActive: boolean,
  ) {
    setSpectatorError(null);
    setSpectatorMessage(null);

    if (!csrfToken) {
      setSpectatorError(
        "Your administrator session does not have a CSRF token. Sign in again.",
      );
      return;
    }

    setSavingSpectator(true);

    try {
      const response =
        await setSpectatorAccess(
          isActive,
          csrfToken,
        );

      setSpectatorMessage(
        isActive
          ? "Spectator access is enabled."
          : response.revoked_sessions
            ? `Spectator access is disabled. ${response.revoked_sessions} session(s) were signed out.`
            : "Spectator access is disabled.",
      );

      await refreshSpectator();
    } catch (currentError) {
      setSpectatorError(
        errorMessage(currentError),
      );
    } finally {
      setSavingSpectator(false);
    }
  }

  async function handleSynchronization(
    fullSync: boolean,
  ) {
    setSyncError(null);
    setSyncMessage(null);

    if (!csrfToken) {
      setSyncError(
        "Your administrator session does not have a CSRF token. Sign in again.",
      );
      return;
    }

    if (
      fullSync &&
      !window.confirm(
        "Run a full ESPN synchronization? This reloads all completed player-week history and may take several minutes.",
      )
    ) {
      return;
    }

    setRunningSync(
      fullSync
        ? "full"
        : "incremental",
    );

    try {
      const result =
        await runSynchronization(
          fullSync,
          csrfToken,
        );

      setSyncMessage(
        `${fullSync ? "Full" : "Incremental"} synchronization completed: ${result.teams_upserted} teams, ${result.matchups_upserted} matchups, and ${result.player_week_scores_upserted} player-week rows processed.`,
      );

      await refreshSyncStatus();
    } catch (currentError) {
      setSyncError(
        errorMessage(currentError),
      );
    } finally {
      setRunningSync(null);
    }
  }

  if (
    spectatorQuery.isPending ||
    syncQuery.isPending
  ) {
    return (
      <section
        className="admin-state"
        aria-live="polite"
      >
        <RefreshCw
          className="spinner"
          aria-hidden="true"
        />

        <h1>Loading administration</h1>

        <p>
          Reading spectator access and ESPN
          synchronization status.
        </p>
      </section>
    );
  }

  if (
    spectatorQuery.isError ||
    syncQuery.isError
  ) {
    const error =
      spectatorQuery.error ??
      syncQuery.error;

    return (
      <section
        className="admin-state"
        aria-live="assertive"
      >
        <h1>Unable to load administration</h1>

        <p>{errorMessage(error)}</p>

        <button
          type="button"
          onClick={() => {
            void spectatorQuery.refetch();
            void syncQuery.refetch();
          }}
        >
          Try again
        </button>
      </section>
    );
  }

  const spectator = spectatorQuery.data;
  const syncStatus = syncQuery.data;

  return (
    <div className="admin-page">
      <header className="admin-header">
        <div>
          <p className="admin-kicker">
            Installation administration
          </p>

          <h1>Administration</h1>

          <p>
            Manage shared spectator access and control
            ESPN synchronization for this installation.
          </p>
        </div>

        <div className="admin-role-badge">
          <ShieldCheck aria-hidden="true" />
          Administrator
        </div>
      </header>

      <section
        className="admin-section"
        aria-labelledby="spectator-heading"
      >
        <div className="admin-section-heading">
          <div>
            <p className="admin-kicker">
              Shared league access
            </p>

            <h2 id="spectator-heading">
              Spectator account
            </h2>
          </div>

          <span
            className={
              spectator.is_active
                ? "admin-status admin-status-active"
                : "admin-status admin-status-disabled"
            }
          >
            {spectator.is_active
              ? "Access enabled"
              : "Access disabled"}
          </span>
        </div>

        {spectator.configured ? (
          <>
            <dl className="admin-detail-grid">
              <div>
                <dt>Username</dt>
                <dd>
                  {spectator.username}
                </dd>
              </div>

              <div>
                <dt>Display name</dt>
                <dd>
                  {spectator.display_name}
                </dd>
              </div>
            </dl>

            <div className="admin-control-panel">
              <div className="admin-control-heading">
                <UserRoundCog aria-hidden="true" />

                <div>
                  <h3>Reset shared password</h3>

                  <p>
                    Resetting the password immediately
                    signs out every existing spectator
                    session.
                  </p>
                </div>
              </div>

              <div className="admin-password-grid">
                <label>
                  New password
                  <input
                    type={
                      showPassword
                        ? "text"
                        : "password"
                    }
                    value={password}
                    minLength={12}
                    maxLength={128}
                    autoComplete="new-password"
                    onChange={(event) => {
                      setPassword(
                        event.target.value,
                      );
                    }}
                  />
                </label>

                <label>
                  Confirm password
                  <input
                    type={
                      showPassword
                        ? "text"
                        : "password"
                    }
                    value={confirmPassword}
                    minLength={12}
                    maxLength={128}
                    autoComplete="new-password"
                    onChange={(event) => {
                      setConfirmPassword(
                        event.target.value,
                      );
                    }}
                  />
                </label>
              </div>

              <div className="admin-actions">
                <button
                  type="button"
                  className="admin-secondary-button"
                  onClick={() => {
                    setShowPassword(
                      (current) => !current,
                    );
                  }}
                >
                  {showPassword ? (
                    <EyeOff aria-hidden="true" />
                  ) : (
                    <Eye aria-hidden="true" />
                  )}

                  {showPassword
                    ? "Hide password"
                    : "Show password"}
                </button>

                <button
                  type="button"
                  className="admin-primary-button"
                  disabled={
                    savingSpectator ||
                    !password ||
                    !confirmPassword
                  }
                  onClick={() => {
                    void handlePasswordReset();
                  }}
                >
                  Reset password
                </button>
              </div>
            </div>

            <div className="admin-access-control">
              <div>
                <h3>Spectator access</h3>

                <p>
                  Disable access to immediately prevent
                  shared spectator sign-ins and revoke
                  active spectator sessions.
                </p>
              </div>

              <button
                type="button"
                className={
                  spectator.is_active
                    ? "admin-danger-button"
                    : "admin-primary-button"
                }
                disabled={savingSpectator}
                onClick={() => {
                  void handleAccessChange(
                    !spectator.is_active,
                  );
                }}
              >
                {spectator.is_active
                  ? "Disable access"
                  : "Enable access"}
              </button>
            </div>
          </>
        ) : (
          <div className="admin-empty-state">
            <UserRoundCog aria-hidden="true" />

            <div>
              <h3>No spectator account</h3>

              <p>
                Create the shared spectator account from
                the setup workflow or a future account
                creation control.
              </p>
            </div>
          </div>
        )}

        {spectatorMessage ? (
          <p
            className="admin-message"
            role="status"
          >
            <CheckCircle2 aria-hidden="true" />
            {spectatorMessage}
          </p>
        ) : null}

        {spectatorError ? (
          <p
            className="admin-error"
            role="alert"
          >
            {spectatorError}
          </p>
        ) : null}
      </section>

      <section
        className="admin-section"
        aria-labelledby="sync-heading"
      >
        <div className="admin-section-heading">
          <div>
            <p className="admin-kicker">
              ESPN provider
            </p>

            <h2 id="sync-heading">
              Synchronization
            </h2>
          </div>

          <span className="admin-status admin-status-active">
            {syncStatus.provider.toUpperCase()}
          </span>
        </div>

        <dl className="admin-detail-grid">
          <div>
            <dt>Season</dt>
            <dd>
              {syncStatus.provider_season_id}
            </dd>
          </div>

          <div>
            <dt>Last synchronized</dt>
            <dd>
              {formatTimestamp(
                syncStatus.last_synced_at,
              )}
            </dd>
          </div>

          <div>
            <dt>Latest run</dt>
            <dd>
              {syncStatus.latest_run?.status ??
                "Unavailable"}
            </dd>
          </div>

          <div>
            <dt>Latest player rows</dt>
            <dd>
              {syncStatus.latest_run
                ?.player_week_scores_upserted ??
                0}
            </dd>
          </div>
        </dl>

        <div className="admin-control-panel">
          <div className="admin-control-heading">
            <Database aria-hidden="true" />

            <div>
              <h3>Run synchronization</h3>

              <p>
                Incremental synchronization refreshes
                recent completed weeks. Full
                synchronization reloads all available
                completed lineup history.
              </p>
            </div>
          </div>

          <div className="admin-actions">
            <button
              type="button"
              className="admin-primary-button"
              disabled={runningSync !== null}
              onClick={() => {
                void handleSynchronization(false);
              }}
            >
              {runningSync === "incremental"
                ? "Synchronizing..."
                : "Run incremental sync"}
            </button>

            <button
              type="button"
              className="admin-secondary-button"
              disabled={runningSync !== null}
              onClick={() => {
                void handleSynchronization(true);
              }}
            >
              {runningSync === "full"
                ? "Running full sync..."
                : "Run full sync"}
            </button>
          </div>
        </div>

        {syncMessage ? (
          <p
            className="admin-message"
            role="status"
          >
            <CheckCircle2 aria-hidden="true" />
            {syncMessage}
          </p>
        ) : null}

        {syncError ? (
          <p
            className="admin-error"
            role="alert"
          >
            {syncError}
          </p>
        ) : null}
      </section>
    </div>
  );
}
