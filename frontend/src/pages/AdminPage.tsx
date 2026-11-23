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
  ListOrdered,
  EyeOff,
  RefreshCw,
  ShieldCheck,
  UserRoundCog,
} from "lucide-react";

import {
  adminQueryKeys,
  pollBackgroundJob,
  replaceProviderCredentials,
  resetSpectatorPassword,
  SPECTATOR_PASSWORD_MAX_LENGTH,
  SPECTATOR_PASSWORD_MIN_LENGTH,
  runSynchronization,
  setSpectatorAccess,
  setStandingsTiebreakerFallback,
  spectatorQueryOptions,
  standingsTiebreakerQueryOptions,
  syncStatusQueryOptions,
} from "../api/admin";
import { ApiError } from "../api/client";
import { analyticsQueryKeys } from "../api/analytics";
import { leagueQueryKeys } from "../api/league";
import { useAuth } from "../auth/AuthProvider";
import { WorkspaceDeletionSection } from "../components/admin/WorkspaceDeletionSection";
import type {
  StandingsTiebreakerRule,
} from "../types/admin";
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


function tiebreakerLabel(
  rule: string | null,
): string {
  if (rule === "TOTAL_POINTS_SCORED") {
    return "Points For";
  }

  if (rule === "H2H_RECORD") {
    return "Head-to-head record";
  }

  if (rule === null) {
    return "Unavailable";
  }

  return rule;
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

  const standingsTiebreakerQuery = useQuery(
    standingsTiebreakerQueryOptions(),
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

  const [providerEspnS2, setProviderEspnS2] =
    useState("");
  const [providerSwid, setProviderSwid] =
    useState("");
  const [
    showProviderCredentials,
    setShowProviderCredentials,
  ] = useState(false);
  const [
    replacingProviderCredentials,
    setReplacingProviderCredentials,
  ] = useState(false);
  const [
    providerCredentialMessage,
    setProviderCredentialMessage,
  ] = useState<string | null>(null);
  const [
    providerCredentialError,
    setProviderCredentialError,
  ] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] =
    useState<string | null>(null);
  const [syncError, setSyncError] =
    useState<string | null>(null);
  const [runningSync, setRunningSync] =
    useState<"incremental" | "full" | null>(
      null,
    );

  const [
    selectedStandingsFallback,
    setSelectedStandingsFallback,
  ] = useState<StandingsTiebreakerRule | null>(
    null,
  );
  const [
    standingsMessage,
    setStandingsMessage,
  ] = useState<string | null>(null);
  const [
    standingsError,
    setStandingsError,
  ] = useState<string | null>(null);
  const [
    savingStandingsFallback,
    setSavingStandingsFallback,
  ] = useState(false);

  const csrfToken = auth.csrfToken;

  async function refreshSpectator() {
    await queryClient.invalidateQueries({
      queryKey: adminQueryKeys.spectator(),
    });
  }

  async function refreshSynchronizedData() {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: adminQueryKeys.syncStatus(),
      }),
      queryClient.invalidateQueries({
        queryKey: leagueQueryKeys.all,
      }),
      queryClient.invalidateQueries({
        queryKey: analyticsQueryKeys.all,
      }),
    ]);
  }

  async function refreshStandingsTiebreaker() {
    await queryClient.invalidateQueries({
      queryKey: adminQueryKeys.standingsTiebreaker(),
    });
  }

  async function handleStandingsFallbackSave() {
    setStandingsError(null);
    setStandingsMessage(null);

    if (!csrfToken) {
      setStandingsError(
        "Your administrator session does not have a CSRF token. Sign in again.",
      );
      return;
    }

    setSavingStandingsFallback(true);

    try {
      const result =
        await setStandingsTiebreakerFallback(
          selectedStandingsFallback ??
            standingsTiebreakerQuery.data
              ?.admin_fallback_rule ??
            "TOTAL_POINTS_SCORED",
          csrfToken,
        );

      setStandingsMessage(
        result.effective_rule_source === "provider"
          ? `Fallback saved. The provider-supplied ${tiebreakerLabel(result.effective_rule)} rule remains authoritative.`
          : `Fallback saved and active: ${tiebreakerLabel(result.effective_rule)}.`,
      );

      await refreshStandingsTiebreaker();
    } catch (currentError) {
      setStandingsError(
        errorMessage(currentError),
      );
    } finally {
      setSavingStandingsFallback(false);
    }
  }

  function sharedPasswordError(): string | null {
    if (password.length < SPECTATOR_PASSWORD_MIN_LENGTH) {
      return `The shared password must contain at least ${SPECTATOR_PASSWORD_MIN_LENGTH} characters.`;
    }

    if (password.length > SPECTATOR_PASSWORD_MAX_LENGTH) {
      return `The shared password cannot contain more than ${SPECTATOR_PASSWORD_MAX_LENGTH} characters.`;
    }

    if (password !== confirmPassword) {
      return "Passwords do not match.";
    }

    return null;
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

    const validationError = sharedPasswordError();

    if (validationError) {
      setSpectatorError(validationError);
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

    if (isActive) {
      // Re-enabling always sets a new shared password; the old one is never revealed.
      const validationError = sharedPasswordError();

      if (validationError) {
        setSpectatorError(validationError);
        return;
      }
    }

    setSavingSpectator(true);

    try {
      const response =
        await setSpectatorAccess(
          isActive,
          csrfToken,
          isActive ? password : undefined,
        );

      if (isActive) {
        setPassword("");
        setConfirmPassword("");
      }

      setSpectatorMessage(
        isActive
          ? "Spectator access is enabled with the new shared password."
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

  async function handleProviderCredentialReplacement() {
    setProviderCredentialError(null);
    setProviderCredentialMessage(null);

    if (!csrfToken) {
      setProviderCredentialError(
        "Your administrator session does not have a CSRF token. Sign in again.",
      );
      return;
    }

    if (
      !providerEspnS2.trim() ||
      !providerSwid.trim()
    ) {
      setProviderCredentialError(
        "Both ESPN_S2 and SWID credentials are required.",
      );
      return;
    }

    if (
      !window.confirm(
        "Replace the stored ESPN credentials? The new values must access this workspace's existing league and active season.",
      )
    ) {
      return;
    }

    setReplacingProviderCredentials(true);

    try {
      const result = await replaceProviderCredentials(
        {
          espn_s2: providerEspnS2,
          swid: providerSwid,
        },
        csrfToken,
      );

      setProviderEspnS2("");
      setProviderSwid("");
      setShowProviderCredentials(false);
      setProviderCredentialMessage(
        `ESPN credentials replaced and validated for season ${result.provider_season_id}.`,
      );
    } catch (currentError) {
      setProviderCredentialError(
        errorMessage(currentError),
      );
    } finally {
      setReplacingProviderCredentials(false);
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
        "Queue a full ESPN synchronization? This reloads all completed player-week history and may take several minutes.",
      )
    ) {
      return;
    }

    const synchronizationLabel = fullSync
      ? "Full"
      : "Incremental";

    setRunningSync(
      fullSync
        ? "full"
        : "incremental",
    );

    try {
      const accepted = await runSynchronization(
        fullSync,
        csrfToken,
      );

      setSyncMessage(
        accepted.existing
          ? `${synchronizationLabel} synchronization is already ${accepted.status.replace("_", " ")}. Monitoring job ${accepted.job_id}.`
          : `${synchronizationLabel} synchronization was queued as job ${accepted.job_id}.`,
      );

      const completedJob = await pollBackgroundJob(
        accepted.job_id,
      );

      if (completedJob.status === "failed") {
        setSyncMessage(null);
        setSyncError(
          completedJob.safe_error_message ??
            "Synchronization failed. Review the persisted job status before trying again.",
        );
        return;
      }

      if (completedJob.status === "canceled") {
        setSyncMessage(null);
        setSyncError(
          "Synchronization was canceled before completion.",
        );
        return;
      }

      if (completedJob.status !== "succeeded") {
        setSyncMessage(null);
        setSyncError(
          "Synchronization ended in an unexpected state. Refresh the page before trying again.",
        );
        return;
      }

      setSyncMessage(
        `${synchronizationLabel} synchronization completed successfully.`,
      );

      await refreshSynchronizedData();
    } catch (currentError) {
      setSyncMessage(null);
      setSyncError(
        errorMessage(currentError),
      );
    } finally {
      setRunningSync(null);
    }
  }

  if (
    spectatorQuery.isPending ||
    syncQuery.isPending ||
    standingsTiebreakerQuery.isPending
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
    syncQuery.isError ||
    standingsTiebreakerQuery.isError
  ) {
    const error =
      spectatorQuery.error ??
      syncQuery.error ??
      standingsTiebreakerQuery.error;

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
            void standingsTiebreakerQuery.refetch();
          }}
        >
          Try again
        </button>
      </section>
    );
  }

  const spectator = spectatorQuery.data;
  const syncStatus = syncQuery.data;
  const standingsTiebreaker =
    standingsTiebreakerQuery.data;
  const displayedStandingsFallback =
    selectedStandingsFallback ??
    standingsTiebreaker.admin_fallback_rule ??
    "TOTAL_POINTS_SCORED";

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
              Shared spectator access
            </h2>
          </div>

          <span
            className={
              spectator.enabled
                ? "admin-status admin-status-active"
                : "admin-status admin-status-disabled"
            }
          >
            {!spectator.configured
              ? "Not configured"
              : spectator.enabled
                ? "Access enabled"
                : "Access disabled"}
          </span>
        </div>

        {spectator.configured ? (
          <>
            <dl className="admin-detail-grid admin-spectator-details">
              <div>
                <dt>Spectator sign-in workspace</dt>
                <dd>
                  {spectator.workspace_slug}
                </dd>
              </div>

              <div>
                <dt>Shared password</dt>
                <dd>Configured (never displayed)</dd>
              </div>

            </dl>

            <div className="admin-control-panel">
              <div className="admin-control-heading">
                <UserRoundCog aria-hidden="true" />

                <div>
                  <h3>
                    {spectator.enabled
                      ? "Change shared password"
                      : "Set a new shared password"}
                  </h3>

                  <p>
                    {spectator.enabled
                      ? "Changing the password immediately signs out every existing spectator session."
                      : "Re-enabling access requires a new shared password. The previous password is never shown or reused."}
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
                    minLength={SPECTATOR_PASSWORD_MIN_LENGTH}
                    maxLength={SPECTATOR_PASSWORD_MAX_LENGTH}
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
                    minLength={SPECTATOR_PASSWORD_MIN_LENGTH}
                    maxLength={SPECTATOR_PASSWORD_MAX_LENGTH}
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

                {spectator.enabled ? (
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
                    Change password
                  </button>
                ) : null}
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
                  spectator.enabled
                    ? "admin-danger-button"
                    : "admin-primary-button"
                }
                disabled={savingSpectator}
                onClick={() => {
                  void handleAccessChange(
                    !spectator.enabled,
                  );
                }}
              >
                {spectator.enabled
                  ? "Disable access"
                  : "Enable access with new password"}
              </button>
            </div>
          </>
        ) : (
          <div className="admin-empty-state">
            <UserRoundCog aria-hidden="true" />

            <div>
              <h3>Shared spectator access is not configured</h3>

              <p>
                Shared spectator access is created during
                workspace setup.
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
        aria-labelledby="standings-tiebreaker-heading"
      >
        <div className="admin-section-heading">
          <div>
            <p className="admin-kicker">
              League rules
            </p>

            <h2 id="standings-tiebreaker-heading">
              Standings tiebreaker
            </h2>
          </div>

          <span className="admin-status admin-status-active">
            {standingsTiebreaker.effective_rule_source ===
            "provider"
              ? "Provider supplied"
              : standingsTiebreaker.effective_rule_source ===
                  "admin_fallback"
                ? "Admin fallback"
                : "Configuration required"}
          </span>
        </div>

        <dl className="admin-detail-grid">
          <div>
            <dt>Provider rule</dt>
            <dd>
              {tiebreakerLabel(
                standingsTiebreaker.provider_rule,
              )}
            </dd>
          </div>

          <div>
            <dt>Effective rule</dt>
            <dd>
              {tiebreakerLabel(
                standingsTiebreaker.effective_rule,
              )}
            </dd>
          </div>
        </dl>

        {standingsTiebreaker.provider_rule_supported ? (
          <div className="admin-control-panel">
            <div className="admin-control-heading">
              <ListOrdered aria-hidden="true" />

              <div>
                <h3>Provider-managed rule</h3>

                <p>
                  The provider supplied a supported standings
                  tiebreaker, so no administrator selection is
                  required. Synchronization keeps this rule
                  aligned with the imported league settings.
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="admin-control-panel">
            <div className="admin-control-heading">
              <ListOrdered aria-hidden="true" />

              <div>
                <h3>Fallback rule required</h3>

                <p>
                  The provider did not supply a supported
                  standings tiebreaker. Choose how teams with
                  equal projected records should be ordered.
                </p>
              </div>
            </div>

            <div className="admin-setting-field">
              <label htmlFor="standings-fallback">
                Standings fallback
              </label>

              <select
                id="standings-fallback"
                value={displayedStandingsFallback}
                disabled={savingStandingsFallback}
                onChange={(event) => {
                  setSelectedStandingsFallback(
                    event.target
                      .value as StandingsTiebreakerRule,
                  );
                }}
              >
                <option value="TOTAL_POINTS_SCORED">
                  Points For
                </option>
                <option value="H2H_RECORD">
                  Head-to-head record
                </option>
              </select>
            </div>

            <div className="admin-actions">
              <button
                type="button"
                className="admin-primary-button"
                disabled={savingStandingsFallback}
                onClick={() => {
                  void handleStandingsFallbackSave();
                }}
              >
                {savingStandingsFallback
                  ? "Saving fallback..."
                  : "Save fallback"}
              </button>
            </div>
          </div>
        )}

        {standingsMessage ? (
          <p
            className="admin-message"
            role="status"
          >
            <CheckCircle2 aria-hidden="true" />
            {standingsMessage}
          </p>
        ) : null}

        {standingsError ? (
          <p
            className="admin-error"
            role="alert"
          >
            {standingsError}
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

        <dl className="admin-detail-grid admin-sync-details">
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

        </dl>

        <div className="admin-control-panel">
          <div className="admin-control-heading">
            <ShieldCheck aria-hidden="true" />

            <div>
              <h3>Replace ESPN credentials</h3>

              <p>
                Update the encrypted ESPN session values
                when access expires. The replacement must
                validate against this workspace&apos;s
                existing claimed league and active season.
              </p>

              <p className="admin-credential-warning">
                Treat ESPN_S2 and SWID like passwords. The
                current stored values cannot be viewed here,
                and a failed replacement leaves them
                unchanged.
              </p>
            </div>
          </div>

          <div className="admin-password-grid">
            <label htmlFor="provider-espn-s2">
              ESPN_S2
              <input
                id="provider-espn-s2"
                type={
                  showProviderCredentials
                    ? "text"
                    : "password"
                }
                value={providerEspnS2}
                autoComplete="off"
                disabled={replacingProviderCredentials}
                onChange={(event) => {
                  setProviderEspnS2(
                    event.target.value,
                  );
                }}
              />
            </label>

            <label htmlFor="provider-swid">
              SWID
              <input
                id="provider-swid"
                type={
                  showProviderCredentials
                    ? "text"
                    : "password"
                }
                value={providerSwid}
                autoComplete="off"
                placeholder="{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}"
                disabled={replacingProviderCredentials}
                onChange={(event) => {
                  setProviderSwid(
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
              disabled={replacingProviderCredentials}
              onClick={() => {
                setShowProviderCredentials(
                  (current) => !current,
                );
              }}
            >
              {showProviderCredentials ? (
                <EyeOff aria-hidden="true" />
              ) : (
                <Eye aria-hidden="true" />
              )}

              {showProviderCredentials
                ? "Hide credentials"
                : "Show credentials"}
            </button>

            <button
              type="button"
              className="admin-primary-button"
              disabled={
                replacingProviderCredentials ||
                !providerEspnS2.trim() ||
                !providerSwid.trim()
              }
              onClick={() => {
                void handleProviderCredentialReplacement();
              }}
            >
              {replacingProviderCredentials
                ? "Validating replacement..."
                : "Validate and replace credentials"}
            </button>
          </div>
        </div>

        {providerCredentialMessage ? (
          <p
            className="admin-message"
            role="status"
          >
            <CheckCircle2 aria-hidden="true" />
            {providerCredentialMessage}
          </p>
        ) : null}

        {providerCredentialError ? (
          <p
            className="admin-error"
            role="alert"
          >
            {providerCredentialError}
          </p>
        ) : null}

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

      <WorkspaceDeletionSection />
    </div>
  );
}
