import {
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Link,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { LockKeyhole } from "lucide-react";

import { ApiError } from "../../api/client";
import { useAuth, type EstablishedSession } from "../../auth/AuthProvider";
import {
  PASSWORD_RESET_COMPLETED_MESSAGE,
  SESSION_REPLACED_MESSAGE,
  throttledMessage,
  unexpectedErrorMessage,
} from "../../auth/accountMessages";
import { safeReturnPath } from "../../auth/returnPath";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";
import { FormField } from "../../components/account/FormField";
import { PasswordField } from "../../components/account/PasswordField";

type LoginMode = "administrator" | "spectator";

interface RedirectState {
  from?: unknown;
}

const SPECTATOR_FAILURE =
  "The workspace or spectator credentials are incorrect.";
const ADMINISTRATOR_FAILURE = "Invalid email or password.";

function loginErrorMessage(error: unknown, mode: LoginMode): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return throttledMessage(error, "sign-in");
    }

    if (error.status === 401 || error.status === 422) {
      // One safe message per mode. Never distinguish unknown accounts,
      // workspaces, inactive users, or malformed identifiers.
      return mode === "administrator"
        ? ADMINISTRATOR_FAILURE
        : SPECTATOR_FAILURE;
    }
  }

  return unexpectedErrorMessage(error);
}

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState<LoginMode>(() =>
    (location.state as { mode?: unknown } | null)?.mode === "spectator"
      ? "spectator"
      : "administrator",
  );
  const [email, setEmail] = useState("");
  const [workspaceSlug, setWorkspaceSlug] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  const destination =
    safeReturnPath((location.state as RedirectState | null)?.from) ?? "/";

  function routeAfterLogin(session: EstablishedSession) {
    if (session.workspaceAccess === "provisional") {
      navigate("/onboarding", { replace: true });
      return;
    }

    navigate(
      session.workspaceAccess === "active" ? destination : "/",
      { replace: true },
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current) {
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setError(null);

    try {
      const session =
        mode === "administrator"
          ? await auth.loginAdministrator({ email, password })
          : await auth.loginSpectator({
              workspace_slug: workspaceSlug,
              password,
            });

      setPassword("");
      routeAfterLogin(session);
    } catch (currentError) {
      setError(loginErrorMessage(currentError, mode));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  function selectMode(nextMode: LoginMode) {
    setMode(nextMode);
    setPassword("");
    setError(null);
  }

  const notice =
    auth.notice === "session_replaced"
      ? SESSION_REPLACED_MESSAGE
      : auth.notice === "password_reset_completed"
        ? PASSWORD_RESET_COMPLETED_MESSAGE
        : null;

  return (
    <AccountLayout
      headingId="login-heading"
      label="League access"
      title="Sign in to continue"
      icon={<LockKeyhole aria-hidden="true" />}
      description={
        <p>
          Administrators sign in with their verified email. Spectators
          use the league workspace name and the shared spectator password.
        </p>
      }
    >
      {notice ? (
        <div className="auth-stack auth-notice-slot">
          <FormAlert
            tone={auth.notice === "session_replaced" ? "notice" : "success"}
          >
            {notice}
          </FormAlert>
        </div>
      ) : null}

      <fieldset className="mode-switch">
        <legend>Sign in as</legend>

        <label>
          <input
            type="radio"
            name="login-mode"
            value="administrator"
            checked={mode === "administrator"}
            onChange={() => {
              selectMode("administrator");
            }}
          />
          Administrator
        </label>

        <label>
          <input
            type="radio"
            name="login-mode"
            value="spectator"
            checked={mode === "spectator"}
            onChange={() => {
              selectMode("spectator");
            }}
          />
          Spectator
        </label>
      </fieldset>

      <form
        className="auth-form"
        aria-label={
          mode === "administrator"
            ? "Administrator sign in"
            : "Spectator sign in"
        }
        noValidate
        onSubmit={(event) => {
          void handleSubmit(event);
        }}
      >
        {mode === "administrator" ? (
          <FormField
            id="administrator-email"
            label="Email"
            name="email"
            type="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            required
            maxLength={320}
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
        ) : (
          <FormField
            id="spectator-workspace"
            label="Workspace"
            name="workspace_slug"
            type="text"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            required
            maxLength={50}
            hint="The short league name your administrator shared."
            value={workspaceSlug}
            onChange={(event) => {
              setWorkspaceSlug(event.target.value);
            }}
          />
        )}

        <PasswordField
          id={
            mode === "administrator"
              ? "administrator-password"
              : "spectator-password"
          }
          label={
            mode === "administrator" ? "Password" : "Spectator password"
          }
          name="password"
          autoComplete="current-password"
          required
          maxLength={128}
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />

        {error ? <FormAlert tone="error">{error}</FormAlert> : null}

        <button
          className="auth-submit"
          type="submit"
          disabled={submitting}
          aria-busy={submitting}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>

      <nav className="auth-links" aria-label="Account options">
        {mode === "administrator" ? (
          <Link to="/forgot-password">Forgot your administrator password?</Link>
        ) : null}
        <Link to="/register">Create a league workspace</Link>
      </nav>
    </AccountLayout>
  );
}
