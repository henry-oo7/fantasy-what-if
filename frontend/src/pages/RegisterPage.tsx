import {
  useEffect,
  useState,
  type FormEvent,
} from "react";
import {
  Navigate,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  BadgeCheck,
  RefreshCw,
  ShieldCheck,
  UserPlus,
} from "lucide-react";

import {
  register,
  requestPreAuthCsrf,
  validateInvitation,
} from "../api/auth";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import type {
  InvitationValidation,
} from "../types/auth";
import "./auth.css";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "An unexpected registration error occurred.";
}

export function RegisterPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const invitationToken =
    searchParams.get("invite")?.trim() ?? "";

  const [invitation, setInvitation] =
    useState<InvitationValidation | null>(null);
  const [invitationError, setInvitationError] =
    useState<string | null>(null);
  const [validating, setValidating] =
    useState(true);

  const [username, setUsername] =
    useState("");
  const [displayName, setDisplayName] =
    useState("");
  const [email, setEmail] =
    useState("");
  const [password, setPassword] =
    useState("");
  const [passwordConfirmation, setPasswordConfirmation] =
    useState("");
  const [registrationError, setRegistrationError] =
    useState<string | null>(null);
  const [submitting, setSubmitting] =
    useState(false);

  useEffect(() => {
    const controller = new AbortController();

    async function loadInvitation() {
      if (!invitationToken) {
        setInvitationError(
          "This registration link does not contain an invitation token.",
        );
        setValidating(false);
        return;
      }

      try {
        const result = await validateInvitation(
          invitationToken,
          controller.signal,
        );

        setInvitation(result);

        if (result.intended_display_name) {
          setDisplayName(
            result.intended_display_name,
          );
        }

        setInvitationError(null);
      } catch (currentError) {
        if (
          currentError instanceof DOMException &&
          currentError.name === "AbortError"
        ) {
          return;
        }

        setInvitationError(
          errorMessage(currentError),
        );
      } finally {
        if (!controller.signal.aborted) {
          setValidating(false);
        }
      }
    }

    void loadInvitation();

    return () => {
      controller.abort();
    };
  }, [invitationToken]);

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

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    if (invitation === null) {
      setRegistrationError(
        "The invitation must be validated before registration.",
      );
      return;
    }

    if (password !== passwordConfirmation) {
      setRegistrationError(
        "Passwords do not match.",
      );
      return;
    }

    setSubmitting(true);
    setRegistrationError(null);

    try {
      const csrf = await requestPreAuthCsrf();

      await register(
        {
          invitation_token: invitationToken,
          username,
          display_name: displayName,
          email: email.trim() || null,
          password,
        },
        csrf.csrf_token,
      );

      await auth.refreshUser();

      navigate("/", {
        replace: true,
      });
    } catch (currentError) {
      setRegistrationError(
        errorMessage(currentError),
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (validating) {
    return (
      <main
        className="app-state"
        aria-live="polite"
      >
        <RefreshCw
          className="spinner"
          aria-hidden="true"
        />
        <h1>Checking your invitation</h1>
        <p>
          Confirming the league and fantasy team
          assigned to this registration link.
        </p>
      </main>
    );
  }

  if (
    invitationError !== null ||
    invitation === null
  ) {
    return (
      <main className="auth-page auth-page-single">
        <section
          className="auth-card invitation-error-card"
          aria-labelledby="invite-error-heading"
        >
          <div className="auth-card-icon auth-card-icon-error">
            <ShieldCheck aria-hidden="true" />
          </div>

          <p className="section-label">
            Invitation unavailable
          </p>

          <h1 id="invite-error-heading">
            This registration link cannot be used
          </h1>

          <p className="auth-card-copy">
            {invitationError ??
              "The invitation is invalid, expired, used, or revoked."}
          </p>

          <button
            className="auth-submit"
            type="button"
            onClick={() => {
              navigate("/login", {
                replace: true,
              });
            }}
          >
            Return to sign in
          </button>
        </section>
      </main>
    );
  }

  const displayNameLocked =
    invitation.intended_display_name !== null;

  return (
    <main className="auth-page">
      <section className="auth-introduction">
        <div className="auth-brand">
          <ShieldCheck aria-hidden="true" />
          <span>Fantasy What-If</span>
        </div>

        <p className="eyebrow">
          Private league invitation
        </p>

        <h1>
          Join your league analytics workspace.
        </h1>

        <p className="auth-description">
          This invitation is assigned to one fantasy
          team and can only be used once.
        </p>

        <div
          className="invitation-summary"
          aria-label="Invitation details"
        >
          <div className="invitation-summary-icon">
            <BadgeCheck aria-hidden="true" />
          </div>

          <div>
            <span>Assigned team</span>
            <strong>{invitation.team_name}</strong>
            <small>
              {invitation.season} season
            </small>
          </div>
        </div>
      </section>

      <section
        className="auth-card"
        aria-labelledby="register-heading"
      >
        <div className="auth-card-icon">
          <UserPlus aria-hidden="true" />
        </div>

        <p className="section-label">
          League registration
        </p>

        <h2 id="register-heading">
          Create your account
        </h2>

        <p className="auth-card-copy">
          Choose credentials for future access to
          the private league dashboard.
        </p>

        <form
          className="auth-form"
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <div className="form-field">
            <label htmlFor="register-username">
              Username
            </label>
            <input
              id="register-username"
              name="username"
              type="text"
              autoComplete="username"
              required
              minLength={3}
              maxLength={50}
              pattern="[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]"
              value={username}
              onChange={(event) => {
                setUsername(event.target.value);
              }}
              aria-describedby="username-help"
            />
            <small id="username-help">
              Letters, numbers, periods, underscores,
              and hyphens.
            </small>
          </div>

          <div className="form-field">
            <label htmlFor="display-name">
              Display name
            </label>
            <input
              id="display-name"
              name="displayName"
              type="text"
              autoComplete="name"
              required
              maxLength={100}
              readOnly={displayNameLocked}
              value={displayName}
              onChange={(event) => {
                setDisplayName(event.target.value);
              }}
            />
            {displayNameLocked ? (
              <small>
                This name was assigned by the league
                administrator.
              </small>
            ) : null}
          </div>

          <div className="form-field">
            <label htmlFor="registration-email">
              Email
              <span className="optional-label">
                Optional
              </span>
            </label>
            <input
              id="registration-email"
              name="email"
              type="email"
              autoComplete="email"
              maxLength={320}
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
              }}
            />
          </div>

          <div className="form-field">
            <label htmlFor="registration-password">
              Password
            </label>
            <input
              id="registration-password"
              name="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              aria-describedby="password-help"
            />
            <small id="password-help">
              Use at least 12 characters and do not
              reuse another account password.
            </small>
          </div>

          <div className="form-field">
            <label htmlFor="password-confirmation">
              Confirm password
            </label>
            <input
              id="password-confirmation"
              name="passwordConfirmation"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={passwordConfirmation}
              onChange={(event) => {
                setPasswordConfirmation(
                  event.target.value,
                );
              }}
            />
          </div>

          {registrationError ? (
            <div
              className="form-error"
              role="alert"
            >
              {registrationError}
            </div>
          ) : null}

          <button
            className="auth-submit"
            type="submit"
            disabled={submitting}
          >
            {submitting
              ? "Creating account..."
              : "Create account"}
          </button>
        </form>

        <p className="auth-help">
          This invitation expires at{" "}
          {new Date(
            invitation.expires_at,
          ).toLocaleString()}.
        </p>
      </section>
    </main>
  );
}
