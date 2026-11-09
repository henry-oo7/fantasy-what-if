import {
  useEffect,
  useRef,
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


const MINIMUM_SYNC_DISPLAY_MS = 8_000;
const COMPLETION_PAUSE_MS = 650;
const PROGRESS_UPDATE_MS = 100;

interface ProgressStage {
  threshold: number;
  label: string;
  detail: string;
}

const PROGRESS_STAGES: ProgressStage[] = [
  {
    threshold: 0,
    label: "Preparing synchronization",
    detail:
      "Opening the encrypted ESPN connection.",
  },
  {
    threshold: 15,
    label: "Validating ESPN access",
    detail:
      "Confirming the saved league credentials.",
  },
  {
    threshold: 30,
    label: "Loading league settings",
    detail:
      "Reading the season, teams, and scoring format.",
  },
  {
    threshold: 45,
    label: "Importing teams",
    detail:
      "Creating the canonical league roster.",
  },
  {
    threshold: 60,
    label: "Importing the schedule",
    detail:
      "Loading regular-season matchups and results.",
  },
  {
    threshold: 75,
    label: "Importing lineup history",
    detail:
      "Loading completed player-week records.",
  },
  {
    threshold: 90,
    label: "Finishing synchronization",
    detail:
      "Waiting for ESPN processing to complete.",
  },
  {
    threshold: 100,
    label: "Installation ready",
    detail:
      "The league has been synchronized and activated.",
  },
];


function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to complete the initial synchronization.";
}


function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}


function stageForProgress(
  progress: number,
): ProgressStage {
  return (
    [...PROGRESS_STAGES]
      .reverse()
      .find(
        (stage) =>
          progress >= stage.threshold,
      ) ?? PROGRESS_STAGES[0]
  );
}


export function FinalizeSetupPanel() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const progressTimer = useRef<number | null>(
    null,
  );

  const [result, setResult] =
    useState<SetupFinalizationResponse | null>(
      null,
    );
  const [error, setError] =
    useState<string | null>(null);
  const [submitting, setSubmitting] =
    useState(false);
  const [progress, setProgress] =
    useState(0);

  const authenticatedAdministrator =
    auth.status === "authenticated" &&
    auth.user?.role === "admin";

  const progressStage =
    stageForProgress(progress);

  useEffect(() => {
    return () => {
      if (progressTimer.current !== null) {
        window.clearInterval(
          progressTimer.current,
        );
      }
    };
  }, []);

  function stopProgressTimer() {
    if (progressTimer.current === null) {
      return;
    }

    window.clearInterval(
      progressTimer.current,
    );

    progressTimer.current = null;
  }

  function startProgressTimer(
    startedAt: number,
  ) {
    stopProgressTimer();

    setProgress(5);

    progressTimer.current =
      window.setInterval(() => {
        const elapsed =
          Date.now() - startedAt;

        const elapsedRatio = Math.min(
          elapsed /
            MINIMUM_SYNC_DISPLAY_MS,
          1,
        );

        /*
         * Ease toward 90%. The progress bar never
         * reaches completion until the backend confirms
         * that setup actually succeeded.
         */
        const estimatedProgress =
          5 +
          85 *
            (
              1 -
              Math.pow(
                1 - elapsedRatio,
                2.2,
              )
            );

        setProgress(
          Math.min(
            90,
            Math.round(
              estimatedProgress,
            ),
          ),
        );
      }, PROGRESS_UPDATE_MS);
  }

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

    const startedAt = Date.now();

    setSubmitting(true);
    startProgressTimer(startedAt);

    try {
      const response = await finalizeSetup(
        auth.csrfToken,
      );

      const elapsed =
        Date.now() - startedAt;

      const remainingDisplayTime =
        Math.max(
          0,
          MINIMUM_SYNC_DISPLAY_MS - elapsed,
        );

      if (remainingDisplayTime > 0) {
        await delay(
          remainingDisplayTime,
        );
      }

      stopProgressTimer();
      setProgress(100);

      await delay(
        COMPLETION_PAUSE_MS,
      );

      setResult(response);
    } catch (currentError) {
      stopProgressTimer();
      setProgress(0);
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
          league and season, import the ESPN schedule
          and completed lineup history, and activate the
          analytics application.
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
              The saved ESPN credentials are decrypted
              only inside the backend.
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

          {submitting ? (
            <section
              className="setup-progress-panel"
              aria-live="polite"
              aria-labelledby="setup-progress-label"
            >
              <div className="setup-progress-heading">
                <div>
                  <p className="setup-kicker">
                    Estimated progress
                  </p>

                  <h3 id="setup-progress-label">
                    {progressStage.label}
                  </h3>

                  <p>
                    {progressStage.detail}
                  </p>
                </div>

                <strong>
                  {progress}%
                </strong>
              </div>

              <div
                className="setup-progress-track"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={progress}
                aria-valuetext={
                  progressStage.label
                }
              >
                <span
                  className="setup-progress-value"
                  style={{
                    width: `${progress}%`,
                  }}
                />
              </div>

              <p className="setup-sync-notice">
                Keep this page open. Progress may pause
                near completion while ESPN finishes
                processing the league history.
              </p>
            </section>
          ) : (
            <button
              type="button"
              className="setup-primary-action"
              onClick={() => {
                void handleFinalize();
              }}
            >
              <RefreshCw aria-hidden="true" />
              Synchronize and activate
            </button>
          )}
        </>
      )}
    </section>
  );
}
