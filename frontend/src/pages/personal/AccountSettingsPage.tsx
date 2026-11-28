import { useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { deleteAccount } from "../../api/accountDeletion";
import { ApiError } from "../../api/client";
import {
  CONNECTION_ERROR_MESSAGE,
  throttledMessage,
} from "../../auth/accountMessages";
import { useAuth } from "../../auth/AuthProvider";
import { FormAlert } from "../../components/account/FormAlert";
import { FormField } from "../../components/account/FormField";
import { PasswordField } from "../../components/account/PasswordField";
import { RemoveLeagueSection } from "./RemoveLeagueSection";
import "./personal.css";

const CONFIRMATION_PHRASE = "DELETE MY ACCOUNT";

function normalizedPhrase(value: string): string {
  return value.trim().split(/\s+/).join(" ").toUpperCase();
}

function deletionErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return throttledMessage(error, "account deletion");
    }

    if (error.code === "invalid_current_password") {
      return "The current password is incorrect.";
    }

    if (error.code === "account_deletion_confirmation_mismatch") {
      return `Type ${CONFIRMATION_PHRASE} exactly as shown.`;
    }

    if (error.status === 409) {
      return "Account deletion is unavailable for this account.";
    }

    return "Your account could not be deleted. Nothing was deleted. Try again later.";
  }

  return CONNECTION_ERROR_MESSAGE;
}

/** /account/settings: the only personal account control, permanent account deletion. */
export function AccountSettingsPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  const confirmed = normalizedPhrase(confirmation) === CONFIRMATION_PHRASE;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current || !confirmed || password.length === 0) {
      return;
    }

    const csrfToken = auth.csrfToken;
    const currentPassword = password;

    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    setPassword("");

    try {
      if (!csrfToken) {
        throw new ApiError("Missing session", 401, "Authentication is required.");
      }

      await deleteAccount(
        { current_password: currentPassword, confirmation },
        csrfToken,
      );
      // Purges every cached query and mutation, and the selected Season
      // and import state they carry, before the signed-out screen renders.
      auth.endSession("account_deleted");
      navigate("/login", { replace: true });
    } catch (currentError) {
      if (currentError instanceof ApiError && currentError.status === 401) {
        auth.endSession();
        navigate("/login", { replace: true });
        return;
      }

      setError(deletionErrorMessage(currentError));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <section className="personal-import" aria-labelledby="account-settings-heading">
      <h1 id="account-settings-heading">Account settings</h1>

      <RemoveLeagueSection />

      <h2 id="delete-account-heading">Delete your account</h2>

      <p>Deleting your account is immediate and cannot be undone. It deletes:</p>

      <ul>
        <li>your sign-in, your email address and your display name;</li>
        <li>every session on every device, which is signed out;</li>
        <li>your saved preferences;</li>
        <li>the ESPN credentials you saved, and your access to every League Season.</li>
      </ul>

      <p>
        League data that other accounts still use stays available to them. League data
        no one uses may be kept for a limited time before it is removed, so it is not
        necessarily deleted at once. You can register again later and import a League
        again, proving access to it with your own ESPN access.
      </p>

      <form
        className="auth-form"
        aria-labelledby="delete-account-heading"
        noValidate
        onSubmit={(event) => {
          void handleSubmit(event);
        }}
      >
        <PasswordField
          id="delete-account-password"
          label="Current password"
          name="current_password"
          autoComplete="current-password"
          required
          maxLength={128}
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />

        <FormField
          id="delete-account-confirmation"
          label={`Type ${CONFIRMATION_PHRASE} to confirm`}
          name="confirmation"
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          maxLength={64}
          value={confirmation}
          onChange={(event) => {
            setConfirmation(event.target.value);
          }}
        />

        {error ? <FormAlert tone="error">{error}</FormAlert> : null}

        <button
          className="auth-submit"
          type="submit"
          disabled={submitting || !confirmed || password.length === 0}
          aria-busy={submitting}
        >
          {submitting ? "Deleting your account…" : "Delete my account"}
        </button>
      </form>
    </section>
  );
}
