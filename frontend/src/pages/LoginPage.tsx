import {
  useState,
  type FormEvent,
} from "react";
import {
  Navigate,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";

import { useAuth } from "../auth/AuthProvider";
import { ApiError } from "../api/client";
import "./auth.css";

interface RedirectState {
  from?: string;
}

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [username, setUsername] =
    useState("");
  const [password, setPassword] =
    useState("");
  const [error, setError] =
    useState<string | null>(null);
  const [submitting, setSubmitting] =
    useState(false);

  if (
    auth.status === "authenticated" &&
    auth.user !== null
  ) {
    return (
      <Navigate
        to="/"
        replace
      />
    );
  }

  const redirectState =
    location.state as RedirectState | null;

  const destination =
    redirectState?.from &&
    redirectState.from.startsWith("/")
      ? redirectState.from
      : "/";

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    setSubmitting(true);
    setError(null);

    try {
      await auth.loginUser({
        username,
        password,
      });

      navigate(destination, {
        replace: true,
      });
    } catch (currentError) {
      if (currentError instanceof ApiError) {
        setError(currentError.detail);
      } else {
        setError(
          "Unable to sign in. Please try again.",
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-introduction">
        <div className="auth-brand">
          <ShieldCheck aria-hidden="true" />
          <span>Fantasy What-If</span>
        </div>

        <p className="eyebrow">
          Private league analytics
        </p>

        <h1>
          See what the standings do not show.
        </h1>

        <p className="auth-description">
          Explore alternate schedules, all-play
          results, league-average matchups, and
          optimal weekly lineups for Jimmy and
          Friends.
        </p>

        <ul className="auth-feature-list">
          <li>Invite-only league access</li>
          <li>Weekly ESPN data synchronization</li>
          <li>Secure server-side sessions</li>
        </ul>
      </section>

      <section
        className="auth-card"
        aria-labelledby="login-heading"
      >
        <div className="auth-card-icon">
          <LockKeyhole aria-hidden="true" />
        </div>

        <p className="section-label">
          League members
        </p>

        <h2 id="login-heading">
          Sign in to continue
        </h2>

        <p className="auth-card-copy">
          Use the account created from your
          private league invitation.
        </p>

        <form
          className="auth-form"
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <div className="form-field">
            <label htmlFor="username">
              Username
            </label>
            <input
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              required
              minLength={3}
              maxLength={50}
              value={username}
              onChange={(event) => {
                setUsername(event.target.value);
              }}
            />
          </div>

          <div className="form-field">
            <label htmlFor="password">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={128}
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
            />
          </div>

          {error ? (
            <div
              className="form-error"
              role="alert"
            >
              {error}
            </div>
          ) : null}

          <button
            className="auth-submit"
            type="submit"
            disabled={submitting}
          >
            {submitting
              ? "Signing in..."
              : "Sign in"}
          </button>
        </form>

        <p className="auth-help">
          Registration requires a private
          invitation from the league administrator.
        </p>
      </section>
    </main>
  );
}
