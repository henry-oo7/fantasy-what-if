import {
  useState,
  type FormEvent,
} from "react";
import {
  useQueryClient,
} from "@tanstack/react-query";
import {
  Eye,
  EyeOff,
  ShieldCheck,
  Users,
} from "lucide-react";
import { Link } from "react-router-dom";

import {
  createSpectatorAccount,
  SPECTATOR_PASSWORD_MAX_LENGTH,
  SPECTATOR_PASSWORD_MIN_LENGTH,
} from "../api/admin";
import { ApiError } from "../api/client";
import {
  setupQueryKeys,
} from "../api/setup";
import { useAuth } from "../auth/AuthProvider";


function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to create the spectator account.";
}


export function SpectatorSetupPanel() {
  const auth = useAuth();
  const queryClient = useQueryClient();

  const [password, setPassword] =
    useState("");
  const [
    confirmPassword,
    setConfirmPassword,
  ] = useState("");
  const [showPassword, setShowPassword] =
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

    if (password.length < SPECTATOR_PASSWORD_MIN_LENGTH) {
      setError(
        `The shared password must contain at least ${SPECTATOR_PASSWORD_MIN_LENGTH} characters.`,
      );
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

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
      // Only the shared password is chosen; the server generates the
      // internal compatibility identity.
      await createSpectatorAccount(
        { password },
        auth.csrfToken,
      );

      setPassword("");
      setConfirmPassword("");

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
          Shared league access
        </p>

        <h2>Create the spectator account</h2>

        <p>
          League members will share one read-only
          account. The administrator can reset this
          password before a new season or whenever
          access should change.
        </p>
      </div>

      <div className="spectator-access-summary">
        <Users aria-hidden="true" />

        <div>
          <strong>
            One shared spectator account
          </strong>

          <p>
            Spectators can view league analytics but
            cannot manage ESPN credentials, run
            synchronization, or open administration.
          </p>
        </div>
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
              create or reset shared spectator access.
            </p>

            <Link to="/login">
              Sign in to continue
            </Link>
          </div>
        </div>
      ) : (
        <form
          className="setup-form spectator-form"
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <div className="spectator-password-heading">
            <div>
              <strong>
                Shared password
              </strong>

              <p>
                Use at least 14 characters. Spectators sign
                in with the workspace slug and this
                password. Resetting
                the password later will sign out all
                existing spectator sessions.
              </p>
            </div>

            <button
              type="button"
              className="setup-secondary-button"
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

              {showPassword ? "Hide" : "Show"}
            </button>
          </div>

          <label className="setup-field">
            <span className="setup-field-label">
              Password
            </span>

            <input
              type={
                showPassword
                  ? "text"
                  : "password"
              }
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              minLength={SPECTATOR_PASSWORD_MIN_LENGTH}
              maxLength={SPECTATOR_PASSWORD_MAX_LENGTH}
              autoComplete="new-password"
              required
            />

            <small>
              This password will be shared with current
              league members.
            </small>
          </label>

          <label className="setup-field">
            <span className="setup-field-label">
              Confirm password
            </span>

            <input
              type={
                showPassword
                  ? "text"
                  : "password"
              }
              value={confirmPassword}
              onChange={(event) => {
                setConfirmPassword(
                  event.target.value,
                );
              }}
              minLength={SPECTATOR_PASSWORD_MIN_LENGTH}
              maxLength={SPECTATOR_PASSWORD_MAX_LENGTH}
              autoComplete="new-password"
              required
            />

            <small>
              Enter the shared password again.
            </small>
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
              ? "Creating spectator account..."
              : "Create spectator account"}
          </button>
        </form>
      )}
    </section>
  );
}
