import {
  CheckCircle2,
  KeyRound,
  ShieldCheck,
  Trophy,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import {
  setupStatusQueryOptions,
} from "../api/setup";
import "./setup.css";


export function SetupPage() {
  const setupQuery = useQuery(
    setupStatusQueryOptions(),
  );

  const setupStatus =
    setupQuery.data?.status ??
    "setup_required";

  const administratorComplete =
    setupStatus === "setup_in_progress";

  return (
    <main className="setup-page">
      <header className="setup-header">
        <div className="setup-brand">
          <ShieldCheck aria-hidden="true" />
          <span>Fantasy What-If</span>
        </div>

        <p className="setup-kicker">
          First-run installation
        </p>

        <h1>Set up your fantasy league</h1>

        <p className="setup-introduction">
          Connect one supported fantasy provider,
          synchronize the selected season, and prepare
          the analytics application.
        </p>
      </header>

      <section
        className="setup-progress"
        aria-label="Setup progress"
      >
        <article
          className={
            administratorComplete
              ? "setup-step setup-step-complete"
              : "setup-step setup-step-current"
          }
        >
          <span className="setup-step-icon">
            {administratorComplete ? (
              <CheckCircle2 aria-hidden="true" />
            ) : (
              <KeyRound aria-hidden="true" />
            )}
          </span>

          <div>
            <span>Step 1</span>
            <strong>
              Create the administrator
            </strong>
            <p>
              Establish the single installation
              administrator account.
            </p>
          </div>
        </article>

        <article
          className={
            administratorComplete
              ? "setup-step setup-step-current"
              : "setup-step"
          }
        >
          <span className="setup-step-icon">
            <Trophy aria-hidden="true" />
          </span>

          <div>
            <span>Step 2</span>
            <strong>
              Connect a fantasy provider
            </strong>
            <p>
              ESPN is available now. Sleeper and Yahoo
              will remain disabled until their adapters
              are implemented.
            </p>
          </div>
        </article>

        <article className="setup-step">
          <span className="setup-step-icon">
            <ShieldCheck aria-hidden="true" />
          </span>

          <div>
            <span>Step 3</span>
            <strong>
              Synchronize and activate
            </strong>
            <p>
              Import the selected season and open the
              analytics dashboard.
            </p>
          </div>
        </article>
      </section>

      <section className="setup-placeholder">
        <p className="setup-kicker">
          Setup workflow
        </p>

        <h2>
          {administratorComplete
            ? "Administrator created"
            : "Administrator account required"}
        </h2>

        <p>
          The installation route guard is now active.
          The interactive account and provider forms
          will be added in the next slice.
        </p>
      </section>
    </main>
  );
}
