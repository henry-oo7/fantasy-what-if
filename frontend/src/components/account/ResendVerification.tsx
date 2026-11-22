import {
  useRef,
  useState,
  type FormEvent,
} from "react";

import { resendVerification } from "../../api/auth";
import { ApiError } from "../../api/client";
import {
  throttledMessage,
  unexpectedErrorMessage,
} from "../../auth/accountMessages";
import { FormAlert } from "./FormAlert";
import { FormField } from "./FormField";

interface ResendVerificationProps {
  /** Known registration email; when absent the visitor enters one. */
  email?: string;
}

type ResendResult =
  | { kind: "accepted"; detail: string }
  | { kind: "error"; message: string; field: boolean };

/**
 * Request another verification email.
 *
 * Shows only the backend's generic accepted wording, which never claims
 * that an account exists.
 */
export function ResendVerification({ email }: ResendVerificationProps) {
  const [enteredEmail, setEnteredEmail] = useState("");
  const [result, setResult] = useState<ResendResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  const targetEmail = email ?? enteredEmail;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current) {
      return;
    }

    if (targetEmail.trim().length === 0) {
      setResult({
        kind: "error",
        message: "Enter the email address you registered with.",
        field: true,
      });
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setResult(null);

    try {
      const response = await resendVerification(targetEmail.trim());

      setResult({ kind: "accepted", detail: response.detail });
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        setResult({
          kind: "error",
          message: throttledMessage(error, "verification email"),
          field: false,
        });
      } else if (error instanceof ApiError && error.status === 422) {
        setResult({
          kind: "error",
          message: "Enter a valid email address.",
          field: true,
        });
      } else {
        setResult({
          kind: "error",
          message: unexpectedErrorMessage(error),
          field: false,
        });
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  const fieldError =
    result?.kind === "error" && result.field ? result.message : null;

  return (
    <form
      className="auth-form"
      aria-label="Resend verification email"
      noValidate
      onSubmit={(event) => {
        void handleSubmit(event);
      }}
    >
      {email === undefined ? (
        <FormField
          id="resend-email"
          label="Registration email"
          name="email"
          type="email"
          autoComplete="email"
          autoCapitalize="none"
          spellCheck={false}
          required
          maxLength={320}
          value={enteredEmail}
          error={fieldError}
          onChange={(event) => {
            setEnteredEmail(event.target.value);
          }}
        />
      ) : null}

      {result?.kind === "accepted" ? (
        <FormAlert tone="success">{result.detail}</FormAlert>
      ) : null}

      {result?.kind === "error" && !(result.field && email === undefined) ? (
        <FormAlert tone="error">{result.message}</FormAlert>
      ) : null}

      <button
        className="auth-secondary-button"
        type="submit"
        disabled={submitting}
        aria-busy={submitting}
      >
        {submitting ? "Sending…" : "Resend verification email"}
      </button>
    </form>
  );
}
