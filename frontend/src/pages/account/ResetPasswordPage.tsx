import {
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import { KeyRound } from "lucide-react";

import { completePasswordReset } from "../../api/auth";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import {
  throttledMessage,
  unexpectedErrorMessage,
} from "../../auth/accountMessages";
import { useOneTimeUrlToken } from "../../auth/useOneTimeUrlToken";
import { AccountLayout } from "../../components/account/AccountLayout";
import { FormAlert } from "../../components/account/FormAlert";
import { PasswordField } from "../../components/account/PasswordField";

const MINIMUM_PASSWORD_LENGTH = 12;

export function ResetPasswordPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const { tokenRef, hadToken: hasToken } = useOneTimeUrlToken();

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [linkInvalid, setLinkInvalid] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (submittingRef.current) {
      return;
    }

    setPasswordError(null);
    setConfirmError(null);
    setFormError(null);

    let invalid = false;

    if (password.length < MINIMUM_PASSWORD_LENGTH) {
      setPasswordError(`Use at least ${MINIMUM_PASSWORD_LENGTH} characters.`);
      invalid = true;
    }

    if (confirmPassword !== password) {
      setConfirmError("Passwords do not match.");
      invalid = true;
    }

    const token = tokenRef.current;

    if (invalid || token === null) {
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);

    try {
      await completePasswordReset({ token, new_password: password });

      tokenRef.current = null;
      setPassword("");
      setConfirmPassword("");
      // Completion revokes administrator sessions and creates none.
      auth.showNotice("password_reset_completed");
      navigate("/login", { replace: true });
    } catch (error) {
      if (error instanceof ApiError && error.code === "invalid_password_reset") {
        tokenRef.current = null;
        setLinkInvalid(true);
      } else if (error instanceof ApiError && error.code === "invalid_password") {
        setPasswordError(error.detail);
      } else if (error instanceof ApiError && error.status === 422) {
        setPasswordError("Choose a password with at least 12 characters.");
      } else if (error instanceof ApiError && error.status === 429) {
        setFormError(throttledMessage(error, "password reset"));
      } else {
        setFormError(unexpectedErrorMessage(error));
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  if (!hasToken || linkInvalid) {
    return (
      <AccountLayout
        headingId="reset-heading"
        label="Administrator recovery"
        title="Reset link unavailable"
        icon={<KeyRound aria-hidden="true" />}
      >
        <FormAlert tone="error">
          {linkInvalid
            ? "This password reset link is invalid, expired, or already used."
            : "This page needs the password reset link from your email."}{" "}
          <Link to="/forgot-password">Request a new reset link</Link>.
        </FormAlert>

        <nav className="auth-links" aria-label="Account options">
          <Link to="/login">Back to sign in</Link>
        </nav>
      </AccountLayout>
    );
  }

  return (
    <AccountLayout
      headingId="reset-heading"
      label="Administrator recovery"
      title="Choose a new password"
      icon={<KeyRound aria-hidden="true" />}
      description={
        <p>
          Every signed-in administrator session will end. Sign in again with
          your administrator email afterwards.
        </p>
      }
    >
      <form
        className="auth-form"
        aria-label="Choose a new password"
        noValidate
        onSubmit={(event) => {
          void handleSubmit(event);
        }}
      >
        <PasswordField
          id="reset-password"
          label="New password"
          name="new-password"
          autoComplete="new-password"
          required
          maxLength={128}
          hint={`At least ${MINIMUM_PASSWORD_LENGTH} characters.`}
          value={password}
          error={passwordError}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />

        <PasswordField
          id="reset-confirm-password"
          label="Confirm new password"
          name="confirm-password"
          autoComplete="new-password"
          required
          maxLength={128}
          value={confirmPassword}
          error={confirmError}
          onChange={(event) => {
            setConfirmPassword(event.target.value);
          }}
        />

        {formError ? <FormAlert tone="error">{formError}</FormAlert> : null}

        <button
          className="auth-submit"
          type="submit"
          disabled={submitting}
          aria-busy={submitting}
        >
          {submitting ? "Saving…" : "Reset password"}
        </button>
      </form>
    </AccountLayout>
  );
}
