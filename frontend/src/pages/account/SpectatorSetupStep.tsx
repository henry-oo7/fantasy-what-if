import { useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  SPECTATOR_PASSWORD_MAX_LENGTH,
  SPECTATOR_PASSWORD_MIN_LENGTH,
} from "../../api/admin";
import {
  getOnboardingSpectator,
  onboardingQueryKeys,
  setUpOnboardingSpectator,
} from "../../api/onboarding";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import {
  CONNECTION_ERROR_MESSAGE,
  LEAGUE_DATA_READY_MESSAGE,
} from "../../auth/accountMessages";
import { FormAlert } from "../../components/account/FormAlert";
import { PasswordField } from "../../components/account/PasswordField";

interface FieldErrors {
  password?: string;
  confirmation?: string;
}

const SAFE_CODES = new Set([
  "onboarding_stage_invalid",
  "spectator_already_configured",
  "spectator_password_invalid",
]);

function setupErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.code !== null && SAFE_CODES.has(error.code)
      ? error.detail
      : "Shared spectator access could not be saved. Try again.";
  }

  return CONNECTION_ERROR_MESSAGE;
}

/**
 * Create the workspace's one shared spectator password.
 *
 * Spectators sign in with the workspace slug and this password. There is
 * no spectator username or email. Values are never trimmed, stored or
 * placed in a URL, and both fields are cleared after success.
 */
export function SpectatorSetupStep() {
  const auth = useAuth();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const spectator = useQuery({
    queryKey: onboardingQueryKeys.spectator(),
    queryFn: ({ signal }) => getOnboardingSpectator(signal),
  });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (pendingRef.current) {
      return;
    }

    const errors: FieldErrors = {};

    if (password.length < SPECTATOR_PASSWORD_MIN_LENGTH) {
      errors.password = `Use at least ${SPECTATOR_PASSWORD_MIN_LENGTH} characters.`;
    } else if (password.length > SPECTATOR_PASSWORD_MAX_LENGTH) {
      errors.password = `Use no more than ${SPECTATOR_PASSWORD_MAX_LENGTH} characters.`;
    }

    if (confirmation !== password) {
      errors.confirmation = "The passwords do not match.";
    }

    setFieldErrors(errors);

    if (Object.keys(errors).length > 0) {
      return;
    }

    pendingRef.current = true;
    setPending(true);
    setError(null);

    try {
      await setUpOnboardingSpectator(password, auth.csrfToken);
      setPassword("");
      setConfirmation("");
      // /api/auth/me now reports complete; the page follows it.
      await auth.refreshUser();
    } catch (setupError) {
      setError(setupErrorMessage(setupError));
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  const slug = spectator.data?.workspace_slug ?? null;

  return (
    <section className="provider-setup" aria-labelledby="spectator-setup-heading">
      <FormAlert tone="success">{LEAGUE_DATA_READY_MESSAGE}</FormAlert>

      <h2 id="spectator-setup-heading">Create shared spectator access</h2>

      <p className="provider-setup-copy">
        You are creating one shared viewing password for your league. League members sign
        in as spectators with your workspace name and this password; they never create
        their own accounts. Share both privately with your league.
      </p>

      {slug ? (
        <dl className="league-target" aria-label="Spectator sign-in">
          <div>
            <dt>Workspace</dt>
            <dd>{slug}</dd>
          </div>
        </dl>
      ) : null}

      <form
        className="auth-form"
        noValidate
        onSubmit={(event) => {
          void handleSubmit(event);
        }}
      >
        <PasswordField
          id="spectator-password"
          label="Shared spectator password"
          hint={`At least ${SPECTATOR_PASSWORD_MIN_LENGTH} characters. Anyone with it can view your league.`}
          autoComplete="new-password"
          maxLength={SPECTATOR_PASSWORD_MAX_LENGTH}
          value={password}
          error={fieldErrors.password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />

        <PasswordField
          id="spectator-password-confirmation"
          label="Confirm shared password"
          autoComplete="new-password"
          maxLength={SPECTATOR_PASSWORD_MAX_LENGTH}
          value={confirmation}
          error={fieldErrors.confirmation}
          onChange={(event) => {
            setConfirmation(event.target.value);
          }}
        />

        {error ? <FormAlert tone="error">{error}</FormAlert> : null}

        <button type="submit" className="auth-submit" disabled={pending} aria-busy={pending}>
          {pending ? "Saving shared access…" : "Create shared spectator access"}
        </button>
      </form>
    </section>
  );
}
