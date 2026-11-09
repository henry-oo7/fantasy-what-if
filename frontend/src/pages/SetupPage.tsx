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
  KeyRound,
  ShieldCheck,
  Trophy,
} from "lucide-react";

import { requestPreAuthCsrf } from "../api/auth";
import { ApiError } from "../api/client";
import {
  bootstrapAdministrator,
  setupQueryKeys,
  setupStatusQueryOptions,
} from "../api/setup";
import { useAuth } from "../auth/AuthProvider";
import "./setup.css";


function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to create the administrator account.";
}


export function SetupPage() {
  const auth = useAuth();
  const queryClient = useQueryClient();

  const setupQuery = useQuery(
    setupStatusQueryOptions(),
  );

  const [username, setUsername] =
    useState("");
  const [displayName, setDisplayName] =
    useState("");
  const [email, setEmail] =
    useState("");
  const [password, setPassword] =
    useState("");
  const [
    confirmPassword,
    setConfirmPassword,
  ] = useState("");
  const [error, setError] =
    useState<string | null>(null);
  const [submitting, setSubmitting] =
    useState(false);

  const setupStatus =
    setupQuery.data?.status ??
    "setup_required";

  const administratorComplete =
    setupStatus === "setup_in_progress";

  const authenticatedAdministrator =
    auth.status === "authenticated" &&
    auth.user?.role === "admin";

  async function handleBootstrap(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();
    setError(null);

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setSubmitting(true);

    try {
      const csrf = await requestPreAuthCsrf();

      await bootstrapAdministrator(
        {
          username,
          display_name: displayName,
          email:
            email.trim().length > 0
              ? email
              : null,
          password,
        },
        csrf.csrf_token,
      );

      await auth.loginUser({
        username,
        password,
      });

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
          Create the installation administrator,
          connect one supported fantasy provider, and
          synchronize the selected season.
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
              remain unavailable until their adapters
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

      {!administratorComplete ? (
        <section className="setup-form-panel">
          <div className="setup-panel-heading">
            <p className="setup-kicker">
              Administrator account
            </p>

            <h2>Create the installation owner</h2>

            <p>
              This account controls provider credentials,
              synchronization, and installation settings.
            </p>
          </div>

          <form
            className="setup-form"
            onSubmit={(event) => {
              void handleBootstrap(event);
            }}
          >
            <label>
              Username
              <input
                value={username}
                onChange={(event) => {
                  setUsername(event.target.value);
                }}
                minLength={3}
                maxLength={50}
                autoComplete="username"
                required
              />
              <small>
                Lowercase letters, numbers, periods,
                underscores, and hyphens.
              </small>
            </label>

            <label>
              Display name
              <input
                value={displayName}
                onChange={(event) => {
                  setDisplayName(event.target.value);
                }}
                maxLength={100}
                autoComplete="name"
                required
              />
            </label>

            <label>
              Email
              <input
                type="email"
                value={email}
                onChange={(event) => {
                  setEmail(event.target.value);
                }}
                maxLength={320}
                autoComplete="email"
              />
              <small>Optional</small>
            </label>

            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value);
                }}
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
                required
              />
              <small>
                At least 12 characters.
              </small>
            </label>

            <label>
              Confirm password
              <input
                type="password"
                value={confirmPassword}
                onChange={(event) => {
                  setConfirmPassword(
                    event.target.value,
                  );
                }}
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
                required
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
                ? "Creating administrator..."
                : "Create administrator"}
            </button>
          </form>
        </section>
      ) : (
        <section className="setup-form-panel">
          <div className="setup-panel-heading">
            <p className="setup-kicker">
              Provider connection
            </p>

            <h2>Administrator created</h2>

            <p>
              {authenticatedAdministrator
                ? `Signed in as ${auth.user?.display_name}.`
                : "Sign in with the administrator account to continue."}
            </p>
          </div>

          <div className="setup-success-message">
            <CheckCircle2 aria-hidden="true" />

            <div>
              <strong>
                Account setup complete
              </strong>

              <p>
                The ESPN provider form will be added in
                the next setup slice.
              </p>
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
