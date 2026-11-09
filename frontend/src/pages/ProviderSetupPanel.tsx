import {
  useState,
  type FormEvent,
} from "react";
import {
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  CheckCircle2,
  Clock3,
  Eye,
  EyeOff,
  ShieldCheck,
} from "lucide-react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  createProviderConnection,
  setupProvidersQueryOptions,
  setupQueryKeys,
} from "../api/setup";
import { useAuth } from "../auth/AuthProvider";


const DEFAULT_SEASON = String(
  new Date().getFullYear(),
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
  const providersQuery = useQuery(
    setupProvidersQueryOptions(),
  );

  const [displayName, setDisplayName] =
    useState("Primary ESPN League");
  const [leagueId, setLeagueId] =
    useState("");
  const [season, setSeason] =
    useState(DEFAULT_SEASON);
  const [espnS2, setEspnS2] =
    useState("");
  const [swid, setSwid] =
    useState("");
  const [showSecrets, setShowSecrets] =
    useState(false);
  const [error, setError] =
    useState<string | null>(null);
  const [submitting, setSubmitting] =
    useState(false);

  const authenticatedAdministrator =
    auth.status === "authenticated" &&
    auth.user?.role === "admin";

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();
    setError(null);

    if (
      !authenticatedAdministrator ||
      !auth.csrfToken
    ) {
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
          display_name: displayName,
          league_id: leagueId,
          season,
          espn_s2:
            espnS2.trim().length > 0
              ? espnS2
              : null,
          swid:
            swid.trim().length > 0
              ? swid
              : null,
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
    <section className="setup-form-panel">
      <div className="setup-panel-heading">
        <p className="setup-kicker">
          Provider connection
        </p>

        <h2>Connect your fantasy league</h2>

        <p>
          ESPN is supported in the first release.
          Provider credentials are encrypted before
          being stored.
        </p>
      </div>

      {providersQuery.isPending ? (
        <p
          className="setup-inline-status"
          aria-live="polite"
        >
          Loading fantasy providers...
        </p>
      ) : null}

      {providersQuery.isError ? (
        <div
          className="setup-inline-error"
          role="alert"
        >
          <p>
            {errorMessage(
              providersQuery.error,
            )}
          </p>

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
        <div
          className="provider-grid"
          aria-label="Fantasy providers"
        >
          {providersQuery.data.providers.map(
            (provider) => (
              <article
                key={provider.provider}
                className={
                  provider.implemented
                    ? "provider-card provider-card-available"
                    : "provider-card provider-card-coming"
                }
              >
                <div className="provider-card-heading">
                  <strong>
                    {provider.display_name}
                  </strong>

                  <span>
                    {provider.implemented ? (
                      <>
                        <CheckCircle2
                          aria-hidden="true"
                        />
                        Available
                      </>
                    ) : (
                      <>
                        <Clock3
                          aria-hidden="true"
                        />
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
            ),
          )}
        </div>
      ) : null}

      {!authenticatedAdministrator ? (
        <div className="setup-auth-required">
          <ShieldCheck aria-hidden="true" />

          <div>
            <strong>
              Administrator sign-in required
            </strong>

            <p>
              Provider credentials can only be managed
              by the installation administrator.
            </p>

            <Link to="/login">
              Sign in to continue
            </Link>
          </div>
        </div>
      ) : (
        <form
          className="setup-form provider-form"
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <label>
            Connection name
            <input
              value={displayName}
              onChange={(event) => {
                setDisplayName(
                  event.target.value,
                );
              }}
              maxLength={120}
              required
            />
            <small>
              A private label for this provider
              connection.
            </small>
          </label>

          <label>
            ESPN league ID
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
              The numeric ID visible in the ESPN
              fantasy league URL.
            </small>
          </label>

          <label>
            Season
            <input
              value={season}
              onChange={(event) => {
                setSeason(event.target.value);
              }}
              inputMode="numeric"
              pattern="[0-9]+"
              autoComplete="off"
              required
            />
            <small>
              For example, 2026.
            </small>
          </label>

          <div className="provider-secret-heading">
            <div>
              <strong>
                Private league credentials
              </strong>

              <p>
                ESPN_S2 and SWID are optional for public
                leagues but both are required for private
                leagues.
              </p>
            </div>

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

          <label>
            ESPN_S2
            <input
              type={
                showSecrets
                  ? "text"
                  : "password"
              }
              value={espnS2}
              onChange={(event) => {
                setEspnS2(event.target.value);
              }}
              autoComplete="off"
            />
          </label>

          <label>
            SWID
            <input
              type={
                showSecrets
                  ? "text"
                  : "password"
              }
              value={swid}
              onChange={(event) => {
                setSwid(event.target.value);
              }}
              autoComplete="off"
              placeholder="{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}"
            />
          </label>

          {error ? (
            <p
              className="setup-form-error"
              role="alert"
            >
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
          >
            {submitting
              ? "Validating ESPN connection..."
              : "Validate and save ESPN connection"}
          </button>
        </form>
      )}
    </section>
  );
}
