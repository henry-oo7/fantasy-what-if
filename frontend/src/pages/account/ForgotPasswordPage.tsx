import {
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link } from "react-router-dom";
import { KeyRound } from "lucide-react";

import { requestPasswordReset } from "../../api/auth";
import { ApiError } from "../../api/client";
import {
  throttledMessage,
  unexpectedErrorMessage,
} from "../../auth/accountMessages";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";
import { FormField } from "../../components/account/FormField";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current) {
      return;
    }

    setFieldError(null);
    setFormError(null);

    if (!email.trim()) {
      setFieldError("Enter your administrator email.");
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);

    try {
      // The backend answers identically whether or not an eligible
      // administrator exists; show its generic wording verbatim.
      const response = await requestPasswordReset({ email: email.trim() });

      setAccepted(response.detail);
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        setFormError(throttledMessage(error, "password reset"));
      } else if (error instanceof ApiError && error.status === 422) {
        setFieldError("Enter a valid email address.");
      } else {
        setFormError(unexpectedErrorMessage(error));
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <AccountLayout
      headingId="forgot-heading"
      label="Administrator recovery"
      title="Reset your password"
      icon={<KeyRound aria-hidden="true" />}
      description={
        accepted === null ? (
          <p>
            Enter the email you use to sign in as an administrator. Spectator
            passwords are managed by your league administrator.
          </p>
        ) : undefined
      }
    >
      {accepted !== null ? (
        <FormAlert tone="success">{accepted}</FormAlert>
      ) : (
        <form
          className="auth-form"
          aria-label="Request a password reset"
          noValidate
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <FormField
            id="forgot-email"
            label="Administrator email"
            name="email"
            type="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            required
            maxLength={320}
            value={email}
            error={fieldError}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />

          {formError ? <FormAlert tone="error">{formError}</FormAlert> : null}

          <button
            className="auth-submit"
            type="submit"
            disabled={submitting}
            aria-busy={submitting}
          >
            {submitting ? "Sending…" : "Send reset instructions"}
          </button>
        </form>
      )}

      <nav className="auth-links" aria-label="Account options">
        <Link to="/login">Back to sign in</Link>
      </nav>
    </AccountLayout>
  );
}
