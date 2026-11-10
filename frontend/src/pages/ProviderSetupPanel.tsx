import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clock3,
  CircleHelp,
  Eye,
  EyeOff,
  ShieldCheck,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  createProviderConnection,
  setupProvidersQueryOptions,
  setupQueryKeys,
} from "../api/setup";
import { useAuth } from "../auth/AuthProvider";

const CURRENT_YEAR = new Date().getFullYear();

const DEFAULT_SEASON = String(CURRENT_YEAR);

const SEASON_OPTIONS = Array.from(
  {
    length: 6,
  },
  (_, index) => String(CURRENT_YEAR + 1 - index),
);

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to connect the fantasy provider.";
}

export function ProviderSetupPanel() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const providersQuery = useQuery(setupProvidersQueryOptions());

  const [leagueId, setLeagueId] = useState("");
  const [season, setSeason] = useState(DEFAULT_SEASON);
  const [espnS2, setEspnS2] = useState("");
  const [swid, setSwid] = useState("");
  const [showSecrets, setShowSecrets] = useState(false);
  const [showCredentialHelp, setShowCredentialHelp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const authenticatedAdministrator =
    auth.status === "authenticated" && auth.user?.role === "admin";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (!authenticatedAdministrator || !auth.csrfToken) {
      setError(
        "Sign in with the installation administrator account to continue.",
      );
      return;
    }

    setSubmitting(true);

    try {
      await createProviderConnection(
        {
          provider: "espn",
          league_id: leagueId,
          season,
          espn_s2: espnS2.trim().length > 0 ? espnS2 : null,
          swid: swid.trim().length > 0 ? swid : null,
        },
        auth.csrfToken,
      );

      await queryClient.invalidateQueries({
        queryKey: setupQueryKeys.all,
      });
    } catch (currentError) {
      setError(errorMessage(currentError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <section className="setup-form-panel">
        <div className="setup-panel-heading">
          <p className="setup-kicker">Provider connection</p>

          <h2>Connect your fantasy league</h2>

          <p>
            ESPN is supported in the first release. Provider credentials are
            encrypted before being stored.
          </p>
        </div>

        {providersQuery.isPending ? (
          <p className="setup-inline-status" aria-live="polite">
            Loading fantasy providers...
          </p>
        ) : null}

        {providersQuery.isError ? (
          <div className="setup-inline-error" role="alert">
            <p>{errorMessage(providersQuery.error)}</p>

            <button
              type="button"
              onClick={() => {
                void providersQuery.refetch();
              }}
            >
              Try again
            </button>
          </div>
        ) : null}

        {providersQuery.data ? (
          <div className="provider-grid" aria-label="Fantasy providers">
            {providersQuery.data.providers.map((provider) => (
              <article
                key={provider.provider}
                className={
                  provider.implemented
                    ? "provider-card provider-card-available"
                    : "provider-card provider-card-coming"
                }
              >
                <div className="provider-card-heading">
                  <strong>{provider.display_name}</strong>

                  <span>
                    {provider.implemented ? (
                      <>
                        <CheckCircle2 aria-hidden="true" />
                        Available
                      </>
                    ) : (
                      <>
                        <Clock3 aria-hidden="true" />
                        Coming soon
                      </>
                    )}
                  </span>
                </div>

                <p>
                  {provider.provider === "espn"
                    ? "Connect a public or private ESPN fantasy football league."
                    : `${provider.display_name} support is planned for a future release.`}
                </p>
              </article>
            ))}
          </div>
        ) : null}

        {!authenticatedAdministrator ? (
          <div className="setup-auth-required">
            <ShieldCheck aria-hidden="true" />

            <div>
              <strong>Administrator sign-in required</strong>

              <p>
                Provider credentials can only be managed by the installation
                administrator.
              </p>

              <Link to="/login">Sign in to continue</Link>
            </div>
          </div>
        ) : (
          <form
            className="setup-form provider-form"
            onSubmit={(event) => {
              void handleSubmit(event);
            }}
          >
            <label className="setup-field">
              <span className="setup-field-label">ESPN league ID</span>
              <input
                value={leagueId}
                onChange={(event) => {
                  setLeagueId(event.target.value);
                }}
                inputMode="numeric"
                pattern="[0-9]+"
                autoComplete="off"
                required
              />
              <small>
                The numeric ID visible in the ESPN fantasy league URL.
              </small>
            </label>

            <label className="setup-field">
              <span className="setup-field-label">Season</span>

              <select
                value={season}
                onChange={(event) => {
                  setSeason(event.target.value);
                }}
                required
              >
                {SEASON_OPTIONS.map((seasonOption) => (
                  <option key={seasonOption} value={seasonOption}>
                    {seasonOption}
                  </option>
                ))}
              </select>

              <small>Select the ESPN fantasy season to import.</small>
            </label>

            <div className="provider-secret-heading">
              <div>
                <strong>Private league credentials</strong>

                <p>
                  ESPN_S2 and SWID are optional for public leagues. Private
                  leagues require both values.
                </p>

                <p className="provider-credential-warning">
                  Treat these values like passwords. Fantasy What-If encrypts
                  them before storing them.
                </p>
              </div>

              <div className="provider-secret-actions">
                <button
                  type="button"
                  className="setup-secondary-button"
                  onClick={() => {
                    setShowCredentialHelp(true);
                  }}
                >
                  <CircleHelp aria-hidden="true" />
                  How to find them
                </button>

                <button
                  type="button"
                  className="setup-secondary-button"
                  onClick={() => {
                    setShowSecrets((current) => !current);
                  }}
                >
                  {showSecrets ? (
                    <EyeOff aria-hidden="true" />
                  ) : (
                    <Eye aria-hidden="true" />
                  )}

                  {showSecrets ? "Hide" : "Show"}
                </button>
              </div>
            </div>

            <label className="setup-field">
              <span className="setup-field-label">ESPN_S2</span>
              <input
                type={showSecrets ? "text" : "password"}
                value={espnS2}
                onChange={(event) => {
                  setEspnS2(event.target.value);
                }}
                autoComplete="off"
              />
            </label>

            <label className="setup-field">
              <span className="setup-field-label">SWID</span>
              <input
                type={showSecrets ? "text" : "password"}
                value={swid}
                onChange={(event) => {
                  setSwid(event.target.value);
                }}
                autoComplete="off"
                placeholder="{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}"
              />
            </label>

            {error ? (
              <p className="setup-form-error" role="alert">
                {error}
              </p>
            ) : null}

            <button type="submit" disabled={submitting}>
              {submitting
                ? "Validating ESPN connection..."
                : "Validate and save ESPN connection"}
            </button>
          </form>
        )}
      </section>

      {showCredentialHelp ? (
        <div
          className="setup-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setShowCredentialHelp(false);
            }
          }}
        >
          <section
            className="setup-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="espn-credential-help-title"
          >
            <header className="setup-modal-header">
              <div>
                <p className="setup-kicker">Private ESPN leagues</p>

                <h2 id="espn-credential-help-title">Find ESPN_S2 and SWID</h2>
              </div>

              <button
                type="button"
                className="setup-modal-close"
                aria-label="Close credential help"
                onClick={() => {
                  setShowCredentialHelp(false);
                }}
              >
                <X aria-hidden="true" />
              </button>
            </header>

            <div className="setup-modal-content">
              <ol>
                <li>
                  Sign in to ESPN in the browser that normally opens the fantasy
                  league.
                </li>
                <li>Open the browser developer tools.</li>
                <li>
                  Open the Application tab in Chromium browsers or the Storage
                  tab in Firefox.
                </li>
                <li>Open Cookies and select the ESPN domain.</li>
                <li>
                  Find <code>espn_s2</code> and copy its complete value into the
                  ESPN_S2 field.
                </li>
                <li>
                  Find <code>SWID</code> and copy its complete value, including
                  braces when present.
                </li>
              </ol>

              <div className="setup-security-notice">
                <ShieldCheck aria-hidden="true" />

                <p>
                  These cookies can provide access to ESPN account data. Never
                  share them in chat, screenshots, GitHub issues, or application
                  logs.
                </p>
              </div>
            </div>

            <footer className="setup-modal-footer">
              <button
                type="button"
                className="setup-primary-action"
                onClick={() => {
                  setShowCredentialHelp(false);
                }}
              >
                I understand
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </>
  );
}
