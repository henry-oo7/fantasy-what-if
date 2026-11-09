import {
  useState,
} from "react";
import {
  CheckCircle2,
  Database,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import {
  useQueryClient,
} from "@tanstack/react-query";
import {
  Link,
  useNavigate,
} from "react-router-dom";

import { ApiError } from "../api/client";
import {
  finalizeSetup,
  setupQueryKeys,
} from "../api/setup";
import { useAuth } from "../auth/AuthProvider";
import type {
  SetupFinalizationResponse,
} from "../types/setup";


function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to complete the initial synchronization.";
}


export function FinalizeSetupPanel() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [result, setResult] =
    useState<SetupFinalizationResponse | null>(
      null,
    );
  const [error, setError] =
    useState<string | null>(null);
  const [submitting, setSubmitting] =
    useState(false);

  const authenticatedAdministrator =
    auth.status === "authenticated" &&
    auth.user?.role === "admin";

  async function handleFinalize() {
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
      const response = await finalizeSetup(
        auth.csrfToken,
      );

      setResult(response);
    } catch (currentError) {
      setError(errorMessage(currentError));
    } finally {
      setSubmitting(false);
    }
  }

  async function openDashboard() {
    await queryClient.invalidateQueries({
      queryKey: setupQueryKeys.all,
    });

    navigate("/", {
      replace: true,
    });
  }

  if (result !== null) {
    return (
      <section className="setup-form-panel">
        <div className="setup-completion">
          <CheckCircle2 aria-hidden="true" />

          <div>
            <p className="setup-kicker">
              Installation active
            </p>

            <h2>Your league is ready</h2>

            <p>
              The initial ESPN synchronization completed
              successfully. Fantasy What-If is ready for
              your league members.
            </p>
          </div>
        </div>

        <dl className="setup-result-grid">
          <div>
            <dt>Teams imported</dt>
            <dd>{result.teams_upserted}</dd>
          </div>

          <div>
            <dt>Matchups imported</dt>
            <dd>{result.matchups_upserted}</dd>
          </div>

          <div>
            <dt>Player-week rows</dt>
            <dd>
              {result.player_week_scores_upserted}
            </dd>
          </div>

          <div>
            <dt>Current week</dt>
            <dd>{result.current_week}</dd>
          </div>

          <div>
            <dt>Provider</dt>
            <dd>{result.provider.toUpperCase()}</dd>
          </div>

          <div>
            <dt>Season</dt>
            <dd>{result.provider_season_id}</dd>
          </div>
        </dl>

        <button
          type="button"
          className="setup-primary-action"
          onClick={() => {
            void openDashboard();
          }}
        >
          Open dashboard
        </button>
      </section>
    );
  }

  return (
    <section className="setup-form-panel">
      <div className="setup-panel-heading">
        <p className="setup-kicker">
          Initial synchronization
        </p>

        <h2>Activate the installation</h2>

        <p>
          Fantasy What-If will create the canonical
          league and season, import the complete ESPN
          schedule and completed lineup history, and
          activate the analytics application.
        </p>
      </div>

      <div className="setup-finalization-summary">
        <article>
          <ShieldCheck aria-hidden="true" />

          <div>
            <strong>
              Encrypted provider connection
            </strong>

            <p>
              The saved ESPN credentials will be
              decrypted only inside the backend.
            </p>
          </div>
        </article>

        <article>
          <Database aria-hidden="true" />

          <div>
            <strong>
              Full initial synchronization
            </strong>

            <p>
              Teams, matchups, completed player weeks,
              and provider capabilities will be imported.
            </p>
          </div>
        </article>
      </div>

      {!authenticatedAdministrator ? (
        <div className="setup-auth-required">
          <ShieldCheck aria-hidden="true" />

          <div>
            <strong>
              Administrator sign-in required
            </strong>

            <p>
              Only the installation administrator can
              run the initial synchronization.
            </p>

            <Link to="/login">
              Sign in to continue
            </Link>
          </div>
        </div>
      ) : (
        <>
          {error ? (
            <p
              className="setup-form-error"
              role="alert"
            >
              {error}
            </p>
          ) : null}

          <button
            type="button"
            className="setup-primary-action"
            disabled={submitting}
            onClick={() => {
              void handleFinalize();
            }}
          >
            {submitting ? (
              <>
                <RefreshCw
                  className="spinner"
                  aria-hidden="true"
                />
                Synchronizing ESPN league...
              </>
            ) : (
              "Synchronize and activate"
            )}
          </button>

          {submitting ? (
            <p
              className="setup-sync-notice"
              aria-live="polite"
            >
              The first synchronization can take
              several minutes for leagues with complete
              lineup history. Keep this page open.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
